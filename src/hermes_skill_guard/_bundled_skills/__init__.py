"""Bundled skill assets shipped with the plugin.

This subpackage exists solely as a container for the on-disk skill
directory (``skill-guard/``). It is loaded via :func:`importlib.resources`
in :mod:`hermes_skill_guard.plugin` so the assets work consistently across
editable installs, wheels, and zip imports.
"""
