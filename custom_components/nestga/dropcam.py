import logging
import json
import requests

from .const import USER_AGENT

_LOGGER = logging.getLogger(__name__)

API_URL = "https://webapi.camera.home.nest.com/api/"

class Dropcam:
    def __init__(self, access_token):
        self._access_token = access_token

    def set_properties(self, properties):
        self.post('dropcams.set_properties', properties)
    
    def get(self, path):
        try:
            response = requests.get(f"{API_URL}{path}", headers=self._default_headers())
            return self._handle_response(response)
        except requests.exceptions.RequestException as e:
            _LOGGER.error(e)
    
    def post(self, path, data):
        try:
            _LOGGER.debug('post %s', data)
            response = requests.post(f"{API_URL}{path}", data=data, headers=self._default_headers())
            return self._handle_response(response)
        except requests.exceptions.RequestException as e:
            _LOGGER.error(e)
    
    def _default_headers(self):
        return {
            "Cookie": f"user_token={self._access_token}",
            "User-Agent": USER_AGENT,
            "Origin": "https://home.nest.com",
            "Referer": "https://home.nest.com/"
        }
    
    def _handle_response(self, response):
        try:
            _LOGGER.debug('dc response %s', response.text)
            return response.json()
        except json.JSONDecodeError as e:
            _LOGGER.error("Failed to decode JSON %s %s", response.text, e)
        