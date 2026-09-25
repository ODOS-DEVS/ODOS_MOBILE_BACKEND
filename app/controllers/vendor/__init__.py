"""Vendor controller, split by domain.

vendor_controller.py holds 79 functions across 3,200 lines. It does not split
as cleanly as the admin controller did, and the reason is worth recording.

fetch_vendor_dashboard reads the vendor's store and their order list, and six
product and order mutations call it afterwards to refresh what gets broadcast
over the websocket. Orders, products and the dashboard are therefore one
cluster: separating them would pull most of the controller into the shared
module and buy nothing.

So the domains that genuinely stand alone live here, and that cluster stays
where it is. vendor_controller.py remains the import surface either way.
"""
