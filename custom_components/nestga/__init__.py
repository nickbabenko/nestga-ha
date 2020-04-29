"""Support for Nest devices."""
from datetime import datetime, timedelta
import logging
import threading

from .nest import Nest
#from .nest.nest import APIError, AuthorizationError
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import (
    CONF_BINARY_SENSORS,
    CONF_FILENAME,
    CONF_MONITORED_CONDITIONS,
    CONF_SENSORS,
    CONF_STRUCTURE,
    EVENT_HOMEASSISTANT_START,
    EVENT_HOMEASSISTANT_STOP,
)
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_connect, dispatcher_send
from homeassistant.helpers.entity import Entity

from . import ga_auth
from .const import DOMAIN

_CONFIGURING = {}
_LOGGER = logging.getLogger(__name__)

SERVICE_CANCEL_ETA = "cancel_eta"
SERVICE_SET_ETA = "set_eta"

DATA_NEST = "nestga"
DATA_NEST_CONFIG = "nestga_config"

SIGNAL_NEST_UPDATE = "nestga_update"

NEST_CONFIG_FILE = "nest.conf"
CONF_ISSUE_TOKEN = "issue_token"
CONF_COOKIE = "cookie"
CONF_REGION = "region"
CONF_USER_ID = "user_id"
CONF_JWT = "jwt"

ATTR_ETA = "eta"
ATTR_ETA_WINDOW = "eta_window"
ATTR_STRUCTURE = "structure"
ATTR_TRIP_ID = "trip_id"

AWAY_MODE_AWAY = "away"
AWAY_MODE_HOME = "home"

ATTR_AWAY_MODE = "away_mode"
SERVICE_SET_AWAY_MODE = "set_away_mode"

SENSOR_SCHEMA = vol.Schema(
    {vol.Optional(CONF_MONITORED_CONDITIONS): vol.All(cv.ensure_list)}
)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_ISSUE_TOKEN): cv.string,
                vol.Required(CONF_COOKIE): cv.string,
                vol.Required(CONF_REGION): cv.string,
                vol.Optional(CONF_STRUCTURE): vol.All(cv.ensure_list, [cv.string]),
                vol.Optional(CONF_SENSORS): SENSOR_SCHEMA,
                vol.Optional(CONF_BINARY_SENSORS): SENSOR_SCHEMA,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)

SET_AWAY_MODE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_AWAY_MODE): vol.In([AWAY_MODE_AWAY, AWAY_MODE_HOME]),
        vol.Optional(ATTR_STRUCTURE): vol.All(cv.ensure_list, [cv.string]),
    }
)

SET_ETA_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ETA): cv.time_period,
        vol.Optional(ATTR_TRIP_ID): cv.string,
        vol.Optional(ATTR_ETA_WINDOW): cv.time_period,
        vol.Optional(ATTR_STRUCTURE): vol.All(cv.ensure_list, [cv.string]),
    }
)

CANCEL_ETA_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_TRIP_ID): cv.string,
        vol.Optional(ATTR_STRUCTURE): vol.All(cv.ensure_list, [cv.string]),
    }
)


def nest_update_event_broker(hass, nest):
    """
    Dispatch SIGNAL_NEST_UPDATE to devices when nest stream API received data.

    Runs in its own thread.
    """
    _LOGGER.debug("Listening for nest.update_event")

    while hass.is_running:
        nest.update_event.wait()

        if not hass.is_running:
            break

        nest.update_event.clear()
        _LOGGER.debug("Dispatching nest data update")
        dispatcher_send(hass, SIGNAL_NEST_UPDATE)

    _LOGGER.debug("Stop listening for nest.update_event")


async def async_setup(hass, config):
    """Set up Nest components."""
    if DOMAIN not in config:
        return True

    conf = config[DOMAIN]
    conf["account"] = {}

    user_id, jwt = await ga_auth.initialize(hass, conf[CONF_ISSUE_TOKEN], conf[CONF_COOKIE], conf[CONF_REGION])
    conf["account"][CONF_USER_ID] = user_id
    conf["account"][CONF_JWT] = jwt

    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_IMPORT},
        )
    )

    # Store config to be used during entry setup
    hass.data[DATA_NEST_CONFIG] = conf

    return True


async def async_setup_entry(hass, entry):
    """Set up Nest from a config entry."""

    _LOGGER.debug("setup entry %s %s %s", entry.domain, entry.title, hass.data[DATA_NEST_CONFIG]["account"][CONF_JWT])

    nest = Nest(
        access_token=hass.data[DATA_NEST_CONFIG]["account"][CONF_JWT],
        user_id=hass.data[DATA_NEST_CONFIG]["account"][CONF_USER_ID],
    )

    _LOGGER.debug("proceeding with setup")
    conf = hass.data.get(DATA_NEST_CONFIG, {})
    hass.data[DATA_NEST] = NestDevice(hass, conf, nest)
    if not await hass.async_add_job(hass.data[DATA_NEST].initialize):
        return False

    for component in "climate", "camera", "sensor", "binary_sensor":
        hass.async_create_task(
            hass.config_entries.async_forward_entry_setup(entry, component)
        )

    def validate_structures(target_structures):
        all_structures = [structure.name for structure in nest.structures]
        for target in target_structures:
            if target not in all_structures:
                _LOGGER.info("Invalid structure: %s", target)

    def set_away_mode(service):
        """Set the away mode for a Nest structure."""
        if ATTR_STRUCTURE in service.data:
            target_structures = service.data[ATTR_STRUCTURE]
            validate_structures(target_structures)
        else:
            target_structures = hass.data[DATA_NEST].local_structure

        for structure in nest.structures:
            if structure.name in target_structures:
                _LOGGER.info(
                    "Setting away mode for: %s to: %s",
                    structure.name,
                    service.data[ATTR_AWAY_MODE],
                )
                structure.away = service.data[ATTR_AWAY_MODE]

    def set_eta(service):
        """Set away mode to away and include ETA for a Nest structure."""
        if ATTR_STRUCTURE in service.data:
            target_structures = service.data[ATTR_STRUCTURE]
            validate_structures(target_structures)
        else:
            target_structures = hass.data[DATA_NEST].local_structure

        for structure in nest.structures:
            if structure.name in target_structures:
                if structure.thermostats:
                    _LOGGER.info(
                        "Setting away mode for: %s to: %s",
                        structure.name,
                        AWAY_MODE_AWAY,
                    )
                    structure.away = AWAY_MODE_AWAY

                    now = datetime.utcnow()
                    trip_id = service.data.get(
                        ATTR_TRIP_ID, f"trip_{int(now.timestamp())}"
                    )
                    eta_begin = now + service.data[ATTR_ETA]
                    eta_window = service.data.get(ATTR_ETA_WINDOW, timedelta(minutes=1))
                    eta_end = eta_begin + eta_window
                    _LOGGER.info(
                        "Setting ETA for trip: %s, "
                        "ETA window starts at: %s and ends at: %s",
                        trip_id,
                        eta_begin,
                        eta_end,
                    )
                    structure.set_eta(trip_id, eta_begin, eta_end)
                else:
                    _LOGGER.info(
                        "No thermostats found in structure: %s, unable to set ETA",
                        structure.name,
                    )

    def cancel_eta(service):
        """Cancel ETA for a Nest structure."""
        if ATTR_STRUCTURE in service.data:
            target_structures = service.data[ATTR_STRUCTURE]
            validate_structures(target_structures)
        else:
            target_structures = hass.data[DATA_NEST].local_structure

        for structure in nest.structures:
            if structure.name in target_structures:
                if structure.thermostats:
                    trip_id = service.data[ATTR_TRIP_ID]
                    _LOGGER.info("Cancelling ETA for trip: %s", trip_id)
                    structure.cancel_eta(trip_id)
                else:
                    _LOGGER.info(
                        "No thermostats found in structure: %s, "
                        "unable to cancel ETA",
                        structure.name,
                    )

    hass.services.async_register(
        DOMAIN, SERVICE_SET_AWAY_MODE, set_away_mode, schema=SET_AWAY_MODE_SCHEMA
    )

    hass.services.async_register(
        DOMAIN, SERVICE_SET_ETA, set_eta, schema=SET_ETA_SCHEMA
    )

    hass.services.async_register(
        DOMAIN, SERVICE_CANCEL_ETA, cancel_eta, schema=CANCEL_ETA_SCHEMA
    )

    @callback
    def start_up(event):
        """Start Nest update event listener."""
        threading.Thread(
            name="Nest update listener",
            target=nest_update_event_broker,
            args=(hass, nest),
        ).start()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_START, start_up)

    @callback
    def shut_down(event):
        """Stop Nest update event listener."""
        nest.update_event.set()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, shut_down)

    _LOGGER.debug("async_setup_nest is done")

    return True


class NestDevice:
    """Structure Nest functions for hass."""

    def __init__(self, hass, conf, nest):
        """Init Nest Devices."""
        self.hass = hass
        self.nest = nest
        self.local_structure = conf.get(CONF_STRUCTURE)

    def initialize(self):
        """Initialize Nest."""
        try:
            # Do not optimize next statement, it is here for initialize
            # persistence Nest API connection.
            for s in self.nest.structures:
                _LOGGER.debug('structure %s', s)
            structure_names = [s.name for s in self.nest.structures]
            if self.local_structure is None:
                self.local_structure = structure_names

        except (OSError) as err:
            _LOGGER.error("Connection error while access Nest web service: %s", err)
            return False
        return True

    def structures(self):
        """Generate a list of structures."""
        return self.nest.structures

    def thermostats(self):
        """Generate a list of thermostats."""
        return self.nest.thermostats

    def smoke_co_alarms(self):
        """Generate a list of smoke co alarms."""
        return []

    def cameras(self):
        """Generate a list of cameras."""
        return self.nest.cameras


class NestSensorDevice(Entity):
    """Representation of a Nest sensor."""

    def __init__(self, structure, device, variable, nest):
        """Initialize the sensor."""
        self.structure = structure
        self.variable = variable
        self._nest = nest

        if device is not None:
            # device specific
            self.device = device
            self._name = f"{self.device.name_long} {self.variable.replace('_', ' ')}"
        else:
            # structure only
            self.device = structure
            self._name = f"{self.structure.name} {self.variable.replace('_', ' ')}"

        self._state = None
        self._unit = None

    @property
    def name(self):
        """Return the name of the nest, if any."""
        return self._name

    @property
    def unit_of_measurement(self):
        """Return the unit the value is expressed in."""
        return self._unit

    @property
    def should_poll(self):
        """Do not need poll thanks using Nest streaming API."""
        return False

    @property
    def unique_id(self):
        """Return unique id based on device serial and variable."""
        return f"{self.device.serial}-{self.variable}"

    @property
    def device_info(self):
        """Return information about the device."""
        if not hasattr(self.device, "name_long"):
            name = self.structure.name
            model = "Structure"
        else:
            name = self.device.name_long
            if self.device.is_thermostat:
                model = "Thermostat"
            elif self.device.is_camera:
                model = "Camera"
            elif self.device.is_smoke_co_alarm:
                model = "Nest Protect"
            else:
                model = None

        return {
            "identifiers": {(DOMAIN, self.device.serial)},
            "name": name,
            "manufacturer": "Nest Labs",
            "model": model,
        }

    def update(self):
        self._nest.update()

    async def async_added_to_hass(self):
        """Register update signal handler."""

        async def async_update_state():
            """Update sensor state."""
            await self.async_update_ha_state(True)

        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_NEST_UPDATE, async_update_state)
        )