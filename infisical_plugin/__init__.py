"""Infisical secret-source plugin for Hermes Agent — pure library package.

Modules here know nothing about the Hermes ``SecretSource`` contract except
``sources.py``, which is the only adapter layer. Everything else (refs, auth,
client, errors) is plain Python so unit tests stay hermetic.
"""
