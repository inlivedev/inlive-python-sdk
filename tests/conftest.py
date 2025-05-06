import pytest
import os
from dotenv import load_dotenv


@pytest.fixture(scope="session", autouse=True)
def load_env():
    """Loads environment variables from .env file before tests run."""
    # Construct the path to the .env file relative to this conftest.py
    # Adjust the path ('../.env') if your conftest.py is deeper
    dotenv_path = os.path.join(os.path.dirname(__file__), "../.env")
    if os.path.exists(dotenv_path):
        load_dotenv(dotenv_path=dotenv_path, override=True)
        print("Loaded environment variables from .env")
    else:
        print(".env file not found, skipping loading.")
