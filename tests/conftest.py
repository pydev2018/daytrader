"""
Pytest configuration and fixtures.

Sets up the test environment so config validation passes
even without a .env file or MT5 installed.
"""

import os
import sys

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
