"""
api.py
------
This module provides API utility functions for interacting with the inLive platform, including token generation, room management, and token validation.
"""

import os

import requests
from fastapi import Request

API_ORIGIN = "https://api.inlive.app"
env_api_origin = os.getenv("API_ORIGIN")
if env_api_origin is not None:
    API_ORIGIN = env_api_origin

HUB_ORIGIN = "https://hub.inlive.app"
env_hub_origin = os.getenv("HUB_ORIGIN")
if env_hub_origin is not None:
    HUB_ORIGIN = env_hub_origin

VERSION = "v1"
URL_PREFIX = f"{HUB_ORIGIN}/{VERSION}"


def generate_access_token():
    """
    Generate an access token using the API key from environment variables.

    Returns:
        tuple: (access_token,refresh_token, error) where access_token is str or None, error is str or None.
    """
    origin = os.getenv("API_ORIGIN")
    api_key = os.getenv("API_KEY")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    resp = requests.post(f"{origin}/v1/keys/accesstoken", headers=headers)
    resp_json = resp.json()
    if resp.status_code == 200:
        return (
            resp_json["data"]["access_token"],
            resp_json["data"]["refresh_token"],
            None,
        )
    return None, None, resp_json


def create_room(roomid: str, token: str):
    """
    Create a new room on the inLive platform.

    Args:
        roomid (str): The room identifier.
        token (str): The access token for authentication.
    Returns:
        tuple: (room_data, error) where room_data is dict or None, error is str or None.
    """
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    resp = requests.post(
        f"{URL_PREFIX}/rooms/create",
        json={"id": roomid},
        headers=headers,
    )
    if resp.status_code >= 200 and resp.status_code < 300:
        return resp.json()["data"], None
    return None, resp.json()


def get_room(room_id: str, token):
    """
    Retrieve information about a room from the inLive platform.

    Args:
        room_id (str): The room identifier.
        token (str): The access token for authentication.
    Returns:
        tuple: (room_data, error) where room_data is dict or None, error is str or None.
    """
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    resp = requests.get(f"{URL_PREFIX}/rooms/{room_id}", headers=headers)
    if resp.status_code == 200:
        return resp.json()["data"], None
    return None, resp.json()


def validate_token(token: str):
    """
    Validate an access token with the inLive API.

    Args:
        token (str): The access token to validate.
    Returns:
        tuple: (status_code, error) where status_code is int, error is str or None.
    """
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = f"{API_ORIGIN}/v1/keys/validate"
    resp = requests.post(url, headers=headers)

    if resp.status_code == 200:
        return 200, None
    if resp.status_code == 403 and "X-Access-Token-Expired" in resp.headers:
        return 403, resp.json()
    return 401, resp.json()


def get_token(request: Request):
    """
    Extract the Bearer token from a FastAPI request.

    Args:
        request (Request): The FastAPI request object.
    Returns:
        str | None: The extracted token, or None if not found.
    """
    token = request.headers.get("Authorization")
    if token is None:
        return None
    token = token.replace("Bearer ", "")
    return token
