"""GA Nest authentication."""
import asyncio
from homeassistant.core import callback
import requests
import logging

_LOGGER = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_14_5) " \
             "AppleWebKit/537.36 (KHTML, like Gecko) " \
             "Chrome/75.0.3770.100 Safari/537.36"
URL_JWT = "https://nestauthproxyservice-pa.googleapis.com/v1/issue_jwt"
NEST_API_KEY = "AIzaSyAdkSIMNc51XGNEAYWasX9UOWkS5P6sZE4"

@callback
def initialize(hass, issue_token, cookie, region):
    """Initialize a local auth provider."""
    access_token = get_access_token(issue_token, cookie)
    return get_jwt(access_token)

def get_access_token(issue_token, cookie):
    headers = {
        'User-Agent': USER_AGENT,
        'Sec-Fetch-Mode': 'cors',
        'X-Requested-With': 'XmlHttpRequest',
        'Referer': 'https://accounts.google.com/o/oauth2/iframe',
        'cookie': cookie
    }
    response = requests.get(url=issue_token, headers=headers)
    return response.json()['access_token']

async def get_jwt(access_token):
    """Authenticate with Google"""
    headers = {
        'User-Agent': USER_AGENT,
        'Authorization': 'Bearer ' + access_token,
        'x-goog-api-key': NEST_API_KEY,
        'Referer': 'https://home.nest.com'
    }
    data = {
        'embed_google_oauth_access_token': True,
        'expire_after': '3600s',
        'google_oauth_access_token': access_token,
        'policy_id': 'authproxy-oauth-policy'
    }
    response = requests.post(
        URL_JWT,
        data=data,
        headers=headers
    )
    user_id = response.json()['claims']['subject']['nestId']['id']
    jwt = response.json()['jwt']
    _LOGGER.debug('jwt %s', jwt)
    return user_id, jwt
