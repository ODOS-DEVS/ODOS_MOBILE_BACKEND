"""Admin controller, split by domain.

admin_controller.py grew to nearly 4,000 lines and 128 functions. The call
graph turned out to be far more modular than the file length suggested -- only
twelve of those functions are used by more than one domain -- so the domains
can be separated without tangling.

app/controllers/admin_controller.py remains as the import surface. Every module
and router that imports from it continues to work unchanged; this package is
where the code actually lives.
"""
