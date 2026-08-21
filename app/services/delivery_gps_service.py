"""GPS delivery tracking service with real-time updates."""

from datetime import datetime, timedelta, timezone
from typing import Optional
from dataclasses import dataclass
from math import radians, sin, cos, sqrt, atan2
import logging

from sqlalchemy import select, and_, desc
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class LocationPoint:
    """GPS location data."""
    latitude: float
    longitude: float
    timestamp: datetime
    accuracy_meters: Optional[float] = None


@dataclass
class DeliveryTrackingUpdate:
    """Delivery tracking update."""
    delivery_id: str
    driver_id: str
    latitude: float
    longitude: float
    timestamp: datetime
    status: str  # in_transit, at_location, delivered
    eta_minutes: Optional[int] = None
    distance_meters: Optional[float] = None


class GeoCalculationService:
    """Service for geographic calculations."""

    @staticmethod
    def calculate_distance(
        lat1: float,
        lon1: float,
        lat2: float,
        lon2: float,
    ) -> float:
        """Calculate distance between two points in meters using Haversine formula."""
        R = 6371000  # Earth radius in meters

        lat1_rad = radians(lat1)
        lat2_rad = radians(lat2)
        delta_lat = radians(lat2 - lat1)
        delta_lon = radians(lon2 - lon1)

        a = (
            sin(delta_lat / 2) ** 2
            + cos(lat1_rad) * cos(lat2_rad) * sin(delta_lon / 2) ** 2
        )
        c = 2 * atan2(sqrt(a), sqrt(1 - a))

        return R * c

    @staticmethod
    def estimate_eta(
        current_location: LocationPoint,
        destination_location: LocationPoint,
        average_speed_kmh: float = 30,
    ) -> int:
        """Estimate time to arrival in minutes."""
        distance_meters = GeoCalculationService.calculate_distance(
            current_location.latitude,
            current_location.longitude,
            destination_location.latitude,
            destination_location.longitude,
        )

        # Convert to km and calculate time
        distance_km = distance_meters / 1000
        time_hours = distance_km / average_speed_kmh
        time_minutes = int(time_hours * 60)

        return max(1, time_minutes)  # At least 1 minute


class DeliveryGPSTracker:
    """Service for tracking deliveries in real-time."""

    # Store recent GPS points in memory (in production, use Redis)
    _location_cache: dict[str, list[LocationPoint]] = {}

    @staticmethod
    def record_location(
        db: Session,
        delivery_id: str,
        driver_id: str,
        latitude: float,
        longitude: float,
        status: str = "in_transit",
        accuracy_meters: Optional[float] = None,
    ) -> bool:
        """Record driver location update."""
        try:
            now = datetime.now(timezone.utc)
            location = LocationPoint(
                latitude=latitude,
                longitude=longitude,
                timestamp=now,
                accuracy_meters=accuracy_meters,
            )

            # Cache location
            if delivery_id not in DeliveryGPSTracker._location_cache:
                DeliveryGPSTracker._location_cache[delivery_id] = []

            # Keep only last 100 points per delivery
            cache = DeliveryGPSTracker._location_cache[delivery_id]
            cache.append(location)
            if len(cache) > 100:
                cache.pop(0)

            # In production: persist to database
            # db.execute(
            #     insert(DeliveryGPSUpdate).values(
            #         delivery_id=delivery_id,
            #         driver_id=driver_id,
            #         latitude=latitude,
            #         longitude=longitude,
            #         status=status,
            #         accuracy_meters=accuracy_meters,
            #         created_at=now,
            #     )
            # )
            # db.commit()

            logger.info(
                f"Recorded location for delivery {delivery_id}: "
                f"{latitude}, {longitude}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to record location: {e}")
            return False

    @staticmethod
    def get_current_location(
        db: Session,
        delivery_id: str,
    ) -> Optional[LocationPoint]:
        """Get current driver location."""
        cache = DeliveryGPSTracker._location_cache.get(delivery_id, [])
        if cache:
            return cache[-1]
        return None

    @staticmethod
    def get_location_history(
        db: Session,
        delivery_id: str,
        limit: int = 50,
    ) -> list[LocationPoint]:
        """Get location history for a delivery."""
        cache = DeliveryGPSTracker._location_cache.get(delivery_id, [])
        return cache[-limit:] if cache else []

    @staticmethod
    def calculate_route_distance(
        db: Session,
        delivery_id: str,
    ) -> float:
        """Calculate total distance traveled."""
        locations = DeliveryGPSTracker.get_location_history(db, delivery_id)
        if len(locations) < 2:
            return 0.0

        total_distance = 0.0
        for i in range(len(locations) - 1):
            current = locations[i]
            next_loc = locations[i + 1]
            distance = GeoCalculationService.calculate_distance(
                current.latitude,
                current.longitude,
                next_loc.latitude,
                next_loc.longitude,
            )
            total_distance += distance

        return total_distance

    @staticmethod
    def get_current_speed(
        db: Session,
        delivery_id: str,
        window_minutes: int = 5,
    ) -> float:
        """Calculate current speed in km/h based on recent movement."""
        locations = DeliveryGPSTracker.get_location_history(db, delivery_id, limit=20)
        if len(locations) < 2:
            return 0.0

        cutoff_time = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
        recent_locations = [
            loc for loc in locations if loc.timestamp >= cutoff_time
        ]

        if len(recent_locations) < 2:
            return 0.0

        first = recent_locations[0]
        last = recent_locations[-1]

        distance_meters = GeoCalculationService.calculate_distance(
            first.latitude,
            first.longitude,
            last.latitude,
            last.longitude,
        )

        time_delta = (last.timestamp - first.timestamp).total_seconds()
        if time_delta <= 0:
            return 0.0

        speed_ms = distance_meters / time_delta
        speed_kmh = speed_ms * 3.6

        return round(speed_kmh, 2)

    @staticmethod
    def update_delivery_status(
        db: Session,
        delivery_id: str,
        status: str,
        reason: Optional[str] = None,
    ) -> bool:
        """Update delivery status (e.g., arrived_at_location, delivered)."""
        try:
            # In production, update the Delivery model
            # db.execute(
            #     update(Delivery)
            #     .where(Delivery.id == delivery_id)
            #     .values(
            #         status=status,
            #         status_updated_at=datetime.now(timezone.utc),
            #         status_reason=reason,
            #     )
            # )
            # db.commit()

            logger.info(
                f"Updated delivery {delivery_id} status to {status}"
                f"{f' (reason: {reason})' if reason else ''}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to update delivery status: {e}")
            return False


class DeliveryAlertService:
    """Service for detecting delivery anomalies."""

    # Alert thresholds
    STATIONARY_THRESHOLD_MINUTES = 15
    MAX_SPEED_THRESHOLD_KMH = 120
    LATE_THRESHOLD_MINUTES = 30

    @staticmethod
    def check_stationary_alert(
        db: Session,
        delivery_id: str,
    ) -> Optional[dict]:
        """Check if driver has been stationary too long."""
        locations = DeliveryGPSTracker.get_location_history(db, delivery_id, limit=10)
        if len(locations) < 2:
            return None

        # Check if all recent locations are within small radius
        first = locations[0]
        max_distance = 0.0

        for location in locations[1:]:
            distance = GeoCalculationService.calculate_distance(
                first.latitude,
                first.longitude,
                location.latitude,
                location.longitude,
            )
            max_distance = max(max_distance, distance)

        # If stationary within 50 meters
        if max_distance < 50:
            time_delta = (locations[-1].timestamp - locations[0].timestamp).total_seconds() / 60
            if time_delta > DeliveryAlertService.STATIONARY_THRESHOLD_MINUTES:
                return {
                    "type": "stationary",
                    "message": f"Driver stationary for {int(time_delta)} minutes",
                    "severity": "warning",
                }

        return None

    @staticmethod
    def check_speeding_alert(
        db: Session,
        delivery_id: str,
    ) -> Optional[dict]:
        """Check if driver is exceeding speed limit."""
        speed = DeliveryGPSTracker.get_current_speed(db, delivery_id)

        if speed > DeliveryAlertService.MAX_SPEED_THRESHOLD_KMH:
            return {
                "type": "speeding",
                "message": f"Driver speed {speed} km/h exceeds limit",
                "severity": "critical",
                "speed": speed,
            }

        return None

    @staticmethod
    def check_eta_breach(
        db: Session,
        delivery_id: str,
        promised_eta: datetime,
    ) -> Optional[dict]:
        """Check if delivery is running late."""
        now = datetime.now(timezone.utc)
        time_until_eta = (promised_eta - now).total_seconds() / 60

        if time_until_eta < -DeliveryAlertService.LATE_THRESHOLD_MINUTES:
            return {
                "type": "late_delivery",
                "message": f"Delivery is {int(-time_until_eta)} minutes late",
                "severity": "warning",
                "minutes_late": int(-time_until_eta),
            }

        return None

    @staticmethod
    def check_route_deviation(
        db: Session,
        delivery_id: str,
        expected_waypoints: list[tuple[float, float]],
        deviation_threshold_meters: float = 1000,
    ) -> Optional[dict]:
        """Check if driver has deviated from expected route."""
        current_location = DeliveryGPSTracker.get_current_location(db, delivery_id)
        if not current_location:
            return None

        # Find closest waypoint
        min_distance = float('inf')
        for lat, lon in expected_waypoints:
            distance = GeoCalculationService.calculate_distance(
                current_location.latitude,
                current_location.longitude,
                lat,
                lon,
            )
            min_distance = min(min_distance, distance)

        if min_distance > deviation_threshold_meters:
            return {
                "type": "route_deviation",
                "message": f"Driver deviated {int(min_distance/1000)} km from route",
                "severity": "warning",
                "deviation_meters": int(min_distance),
            }

        return None
