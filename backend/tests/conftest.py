"""Tests never call the live traffic, air-quality or pollen APIs."""
import os

os.environ["TRAFFIC_DISABLED"] = "1"
