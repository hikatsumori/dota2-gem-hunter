"""
Dota 2 Steam Market Rare Socket Scanner

A GitHub Actions + Supabase + Discord scanner for detecting rare socketed
items (Ethereal/Prismatic gems) on the Steam Community Market.
"""

from src.main import main, scan_item_type

__version__ = "0.1.0"
__author__ = "Steam Market Hunter"

__all__ = ["main", "scan_item_type"]
