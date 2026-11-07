"""Shipped builtin tool sources.

Each `.py` here is generated from a WebUI tool export by `tools/convert_builtins.py`, and
`manifest.json` records which function names each one exposes.

These are loaded by `builtin_registry.py` through `spec_from_loader`, so no import happens at
package import time. That is deliberate: one of these files is 87 KB, and baking all nine into
every application start would be a cost paid even by an install with every tool switched off.

Empty on purpose. Nothing here should be imported by name.
"""
