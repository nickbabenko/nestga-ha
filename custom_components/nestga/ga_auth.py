"""GA Nest authentication."""
import asyncio
from homeassistant.core import callback
import requests
import logging

_LOGGER = logging.getLogger(__name__)

from .const import DATA_NEST_CONFIG, CONF_JWT, CONF_USER_ID, CONF_TRANSPORT_URL

API_HOSTNAME = 'home.nest.com'
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_14_5) " \
             "AppleWebKit/537.36 (KHTML, like Gecko) " \
             "Chrome/75.0.3770.100 Safari/537.36"
URL_JWT = "https://nestauthproxyservice-pa.googleapis.com/v1/issue_jwt"
URL_AUTH = 'https://' + API_HOSTNAME + '/session'
NEST_API_KEY = "AIzaSyAdkSIMNc51XGNEAYWasX9UOWkS5P6sZE4"

def initialize(hass, issue_token, cookie, region):
    """Initialize a local auth provider."""
    google_access_token = get_google_access_token(issue_token, cookie)
    jwt = get_jwt(google_access_token)
    transport_url, userid = get_token(jwt)
    _LOGGER.debug('authenticated %s %s %s', jwt, transport_url, userid)
    hass.data[DATA_NEST_CONFIG]["account"][CONF_JWT] = jwt
    hass.data[DATA_NEST_CONFIG]["account"][CONF_TRANSPORT_URL] = transport_url
    hass.data[DATA_NEST_CONFIG]["account"][CONF_USER_ID] = userid

def get_google_access_token(issue_token, cookie):
    headers = {
        'User-Agent': USER_AGENT,
        'Sec-Fetch-Mode': 'cors',
        'X-Requested-With': 'XmlHttpRequest',
        'Referer': 'https://accounts.google.com/o/oauth2/iframe',
        'cookie': cookie
    }
    response = requests.get(url=issue_token, headers=headers)
    return response.json()['access_token']

def get_jwt(google_access_token):
    """Authenticate with Google"""
    headers = {
        'User-Agent': USER_AGENT,
        'Authorization': 'Bearer ' + google_access_token,
        'x-goog-api-key': NEST_API_KEY,
        'Referer': 'https://home.nest.com'
    }
    data = {
        'embed_google_oauth_access_token': True,
        'expire_after': '3600s',
        'google_oauth_access_token': google_access_token,
        'policy_id': 'authproxy-oauth-policy'
    }
    response = requests.post(
        URL_JWT,
        data=data,
        headers=headers
    )
    jwt = response.json()['jwt']
    return jwt

def get_token(access_token):
    headers={
        'Authorization': 'Basic ' + access_token,
        'User-Agent': USER_AGENT,
        'Cookie': 'G_ENABLED_IDPS=google; eu_cookie_accepted=1; viewer-volume=0.5; cztoken=' + access_token
    }
    response = requests.get(
        URL_AUTH,
        headers=headers
    )
    json = response.json()
    transport_url = json['urls']['transport_url']
    userid = json['userid']
    return transport_url, userid