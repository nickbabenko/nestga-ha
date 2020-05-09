"""Config flow to configure Nest."""
import asyncio
from collections import OrderedDict
import logging
import os

import async_timeout
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util.json import load_json

from .const import DOMAIN, CONF_ISSUE_TOKEN, CONF_COOKIE, CONF_REGION
from .ga_auth import get_google_access_token

DATA_FLOW_IMPL = "nest_ga_flow_implementation"
_LOGGER = logging.getLogger(__name__)

@callback
def register_flow_implementation(hass, domain, name, gen_authorize_url, convert_code):
    """Register a flow implementation.

    domain: Domain of the component responsible for the implementation.
    name: Name of the component.
    gen_authorize_url: Coroutine function to generate the authorize url.
    convert_code: Coroutine function to convert a code to an access token.
    """
    if DATA_FLOW_IMPL not in hass.data:
        hass.data[DATA_FLOW_IMPL] = OrderedDict()

    hass.data[DATA_FLOW_IMPL][domain] = {
        "domain": domain,
        "name": name,
        "gen_authorize_url": gen_authorize_url,
        "convert_code": convert_code,
    }


class NestAuthError(HomeAssistantError):
    """Base class for Nest auth errors."""


class CodeInvalid(NestAuthError):
    """Raised when invalid authorization code."""


@config_entries.HANDLERS.register(DOMAIN)
class NestFlowHandler(config_entries.ConfigFlow):
    """Handle a Nest config flow."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_PUSH

    def __init__(self):
        """Initialize the Nest config flow."""
        self.issue_token = None
        self.cookie = None
        self.region = None

    async def async_step_user(self, user_input=None):
        """Handle a flow initialized by the user."""
        return await self.async_step_init(user_input)

    async def async_step_init(self, user_input=None):
        """Handle a flow start."""
        errors = {}

        if self.hass.config_entries.async_entries(DOMAIN):
            return self.async_abort(reason="already_setup")

        if user_input is not None:
            can_connect = await self.hass.async_add_executor_job(
                try_connection,
                user_input[CONF_ISSUE_TOKEN],
                user_input[CONF_COOKIE]
            )

            if can_connect:
                return self.async_create_entry(
                    title=DOMAIN, data=user_input
                )
            
            errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(CONF_ISSUE_TOKEN): str,
                vol.Required(CONF_COOKIE): str,
                vol.Required(CONF_REGION, default="us"): str
            }),
            errors=errors
        )
    
    async def async_step_import(self, user_input):
        """Import a config entry.
        Special type of import, we're not actually going to store any data.
        Instead, we're going to rely on the values that are in config file.
        """
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        return self.async_create_entry(title="configuration.yaml", data={})

def try_connection(issue_token, cookie):
    access_token = get_google_access_token(issue_token, cookie)
    return access_token is not None