import logging
import requests
import copy
import uuid
import json
import collections
import datetime

from dateutil.parser import parse as parse_time

_LOGGER = logging.getLogger(__name__)

API_URL = "https://home.nest.com"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_14_5) " \
             "AppleWebKit/537.36 (KHTML, like Gecko) " \
             "Chrome/75.0.3770.100 Safari/537.36"

DEVICE = 'device'
METADATA = 'metadata'
STRUCTURE = 'structure'
THERMOSTAT = 'thermostat'
SMOKE_CO_ALARM = 'smoke_co_alarm'
CAMERA = 'camera'
WHERE = 'where'

AWAY_MAP = {'on': 'away',
            'away': 'away',
            'off': 'home',
            'home': 'home',
            True: 'away',
            False: 'home'}

FAN_MAP = {'auto on': False,
           'on': True,
           'auto': False,
           '1': True,
           '0': False,
           1: True,
           0: False,
           True: True,
           False: False}

LowHighTuple = collections.namedtuple('LowHighTuple', ('low', 'high'))

# https://developers.nest.com/documentation/api-reference/overview#targettemperaturef
MINIMUM_TEMPERATURE_F = 50
MAXIMUM_TEMPERATURE_F = 90
# https://developers.nest.com/documentation/api-reference/overview#targettemperaturec
MINIMUM_TEMPERATURE_C = 9
MAXIMUM_TEMPERATURE_C = 32

BUCKETS = {
    #"topaz",
    #"kryptonite",
    "link": THERMOSTAT,
    "device": THERMOSTAT,
    "schedule": THERMOSTAT,
    "shared": THERMOSTAT,
    "where": WHERE,
    "quartz": CAMERA,
    "structure": STRUCTURE
}

SIMULATOR_SNAPSHOT_URL = \
    'https://developer.nest.com' \
    '/simulator/api/v1/nest/devices/camera/snapshot'
SIMULATOR_SNAPSHOT_PLACEHOLDER_URL = \
    'https://media.giphy.com/media/WCwFvyeb6WJna/giphy.gif'

def nest_object(type, id, data, nest_api):
    _LOGGER.debug('find object for type %s', type)
    for cls in NestBase.__subclasses__():
        if cls.is_handler_for(type):
            _LOGGER.debug('found %s', cls)
            return cls(id, data, nest_api)
    for cls in Device.__subclasses__():
        if cls.is_handler_for(type):
            _LOGGER.debug('found %s', cls)
            return cls(id, data, nest_api)
    _LOGGER.error('Failed to find device with ID %s and type %s', id, type)
    raise ValueError

class Nest(object):
    def __init__(self, access_token, user_id):
        self._access_token = access_token
        self._user_id = user_id
        self._storage = Storage()
        self._last_update = None
        self.update()
    
    @property
    def structures(self):
        return self._storage.get(STRUCTURE)
    
    @property
    def cameras(self):
        return self._storage.get(CAMERA)
    
    @property
    def thermostats(self):
        return self._storage.get(THERMOSTAT)
    
    @property
    def wheres(self):
        return self._storage.get(WHERE)
    
    def thermostat(self, id):
        return self._storage.get(THERMOSTAT, id)
    
    def camera(self, id):
        return self._storage.get(CAMERA, id)
    
    def structure(self, id):
        return self._storage.get(STRUCTURE, id)
    
    def where(self, id):
        return self._storage.get(WHERE, id)
    
    def _should_update(self):
        return self._last_update is None or self._last_update < datetime.datetime.now() - datetime.timedelta(seconds=30)
    
    def update(self):
        if not self._should_update():
            return

        try:
            response = self.post(
                f"/api/0.1/user/{self._user_id}/app_launch",
                {
                    "known_bucket_types": list(BUCKETS.keys()),
                    "known_bucket_versions": [],
                }
            )

            _LOGGER.debug('Fetched nest devices %d', len(response['updated_buckets']))

            for received_bucket in response["updated_buckets"]:
                sensor_data = received_bucket["value"]
                for bucket in BUCKETS.keys():
                    if received_bucket["object_key"].startswith(f"{bucket}."):
                        udid = received_bucket["object_key"].replace(f"{bucket}.", "")
                        item_type = BUCKETS[bucket]
                        item = self._storage.get(item_type, udid)
                        if item is None:
                            item = nest_object(item_type, udid, sensor_data, self)
                            _LOGGER.info('Adding device %s %s %s', item_type, udid, item)
                            self._storage.add(item_type, udid, item)
                        else:
                            item.set(sensor_data)
            
            self._last_update = datetime.datetime.utcnow()
        except requests.exceptions.RequestException as e:
            _LOGGER.error(e)
            _LOGGER.error('Failed to update, trying again')
            raise e
        except KeyError as e:
            _LOGGER.error(e)
            _LOGGER.debug('Failed to update, trying to log in again')
            raise e
    
    def get(self, path):
        try:
            response = requests.get(f"{API_URL}{path}", headers=self._default_headers())
            return self._handle_response(response)
        except requests.exceptions.RequestException as e:
            _LOGGER.error(e)
    
    def post(self, path, data):
        try:
            response = requests.post(f"{API_URL}{path}", json=data, headers=self._default_headers())
            return self._handle_response(response)
        except requests.exceptions.RequestException as e:
            _LOGGER.error(e)
    
    def _handle_response(self, response):
        try:
            return response.json()
        except json.JSONDecodeError as e:
            _LOGGER.error("Failed to decode JSON %s %s", response.text, e)
    
    def _default_headers(self):
        return {
            "Authorization": f"Basic {self._access_token}",
            "User-Agent": USER_AGENT
        }
                    
class Storage(object):
    def __init__(self):
        self._items = {}
    
    def get(self, type, id = None):
        _LOGGER.debug('get device %s %s', type, id)
        if type in self._items:
            list = self._items[type]
        else:
            _LOGGER.debug('no devices for type %s', type)
            list = {}
        if id is None:
            return list.values()
        if id in list:
            return list[id]
        else:
            _LOGGER.error("Failed to find device with ID %s %s", type, id)
    
    def add(self, type, id, item):
        if type not in self._items:
            self._items[type] = {}
        self._items[type][id] = item

class NestBase(object):
    def __init__(self, id, data, nest_api):
        self._id = id
        self._data = data
        self._nest_api = nest_api
        _LOGGER.debug('base %s', data)

    def __str__(self):
        return '<%s: %s>' % (self.__class__.__name__, self._id)

    def set(self, data):
        self._data.update(data)

    @property
    def _weather(self):
        raise NotImplementedError("Deprecated Nest API")
        # merge_code = self.postal_code + ',' + self.country_code
        # return self._nest_api._weather[merge_code]

    @property
    def weather(self):
        raise NotImplementedError("Deprecated Nest API")
        # return Weather(self._weather, self._local_time)

    @property
    def id(self):
        return self._id

    @property
    def serial(self):
        return self._id

    @property
    def _repr_name(self):
        return self.serial

class Where(NestBase):
    @classmethod
    def is_handler_for(self, type):
        return type == WHERE

    def __init__(self, id, data, nest_api):
        super().__init__(id, data, nest_api)
        self._wheres = {}
        for where in data['wheres']:
            self._wheres[where['where_id']] = where['name']
    
    @property
    def wheres(self):
        return self._data.get('wheres')
    
    def where(self, where_id):
        if where_id in self._wheres:
            return self._wheres[where_id]

class Device(NestBase):
    @classmethod
    def is_handler_for(self, type):
        return False

    @property
    def _devices(self):
        return None #self._nest_api._devices

    @property
    def _repr_name(self):
        if self.name:
            return self.name

        return self.where

    def __repr__(self):
        return str(self._data)

    @property
    def name(self):
        return self._data.get('name')

    @name.setter
    def name(self, value):
        raise NotImplementedError("Needs updating with new API")
        # self._set('shared', {'name': value})

    @property
    def name_long(self):
        return self._data.get('name_long')

    @property
    def device_id(self):
        return self._data.get('device_id')

    @property
    def online(self):
        return self._data.get('is_online')

    @property
    def software_version(self):
        return self._data.get('software_version')

    @property
    def structure_id(self):
        return self._data.get('structure_id')

    @property
    def structure(self):
        return self._nest_api.structure(self.structure_id)

    @property
    def where(self):
        if self.where_id is not None:
            wheres = self._nest_api.wheres
            if len(wheres) > 0:
                where = list(wheres)[0]
                return where.where(self.where_id)

    @property
    def where_id(self):
        return self._data.get('where_id')

    @where.setter
    def where(self, value):
        value = value.lower()
        ident = self.structure.wheres.get(value)

        if ident is None:
            self.structure.add_where(value)
            ident = self.structure.wheres[value]

        self._set('device', {'where_id': ident})

    @property
    def description(self):
        return self._data.get('name_long')

    @property
    def is_thermostat(self):
        return False

    @property
    def is_camera(self):
        return False

    @property
    def is_smoke_co_alarm(self):
        return False


class Thermostat(Device):
    @classmethod
    def is_handler_for(self, type):
        return type == THERMOSTAT
    
    @property
    def is_thermostat(self):
        return True
    
    @property
    def name(self):
        return self.where
    
    @property
    def name_long(self):
        return self.name

    @property
    def _shared(self):
        raise NotImplementedError("Deprecated Nest API")
        # return self._nest_api._status['shared'][self._serial]

    @property
    def _track(self):
        raise NotImplementedError("Deprecated Nest API")
        # return self._nest_api._status['track'][self._serial]

    @property
    def fan(self):
        # FIXME confirm this is the same as old havac_fan_state
        return self._data.get('fan_timer_active')
    
    @property
    def structure_id(self):
        # Thermo stat structure is given through its link bucket, not the standard structure_id
        return self._data.get('structure').replace('structure.', '')

    @fan.setter
    def fan(self, value):
        mapped_value = FAN_MAP.get(value, False)
        if mapped_value is None:
            raise ValueError("Only True and False supported")

        self._set('devices/thermostats', {'fan_timer_active': mapped_value})

    @property
    def fan_timer(self):
        return self._data.get('fan_timer_duration')

    @fan_timer.setter
    def fan_timer(self, value):
        self._set('devices/thermostats', {'fan_timer_duration': value})

    @property
    def humidity(self):
        return self._data.get('humidity')

    @property
    def target_humidity(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['target_humidity']

    @target_humidity.setter
    def target_humidity(self, value):
        raise NotImplementedError("No longer available in Nest API")
    #    if value == 'auto':

    #        if self._weather['current']['temp_c'] >= 4.44:
    #            hum_value = 45
    #        elif self._weather['current']['temp_c'] >= -1.11:
    #            hum_value = 40
    #        elif self._weather['current']['temp_c'] >= -6.67:
    #            hum_value = 35
    #        elif self._weather['current']['temp_c'] >= -12.22:
    #            hum_value = 30
    #        elif self._weather['current']['temp_c'] >= -17.78:
    #            hum_value = 25
    #        elif self._weather['current']['temp_c'] >= -23.33:
    #            hum_value = 20
    #        elif self._weather['current']['temp_c'] >= -28.89:
    #            hum_value = 15
    #        elif self._weather['current']['temp_c'] >= -34.44:
    #            hum_value = 10
    #    else:
    #        hum_value = value

    #    if float(hum_value) != self._data['target_humidity']:
    #        self._set('device', {'target_humidity': float(hum_value)})

    @property
    def mode(self):
        return self._data.get('target_temperature_type')

    @mode.setter
    def mode(self, value):
        self._set('devices/thermostats', {'hvac_mode': value.lower()})

    @property
    def has_leaf(self):
        return self._data.get('has_leaf')

    @property
    def hvac_ac_state(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._shared['hvac_ac_state']

    @property
    def hvac_cool_x2_state(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._shared['hvac_cool_x2_state']

    @property
    def hvac_heater_state(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._shared['hvac_heater_state']

    @property
    def hvac_aux_heater_state(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._shared['hvac_aux_heater_state']

    @property
    def hvac_heat_x2_state(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._shared['hvac_heat_x2_state']

    @property
    def hvac_heat_x3_state(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._shared['hvac_heat_x3_state']

    @property
    def hvac_alt_heat_state(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._shared['hvac_alt_heat_state']

    @property
    def hvac_alt_heat_x2_state(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._shared['hvac_alt_heat_x2_state']

    @property
    def hvac_emer_heat_state(self):
        raise NotImplementedError(
            "No longer available in Nest API. See "
            "is_using_emergency_heat instead")
        # return self._shared['hvac_emer_heat_state']

    @property
    def is_using_emergency_heat(self):
        return self._data.get('is_using_emergency_heat')

    @property
    def label(self):
        return self._data.get('label')

    @property
    def local_ip(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['local_ip']

    @property
    def last_ip(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._track['last_ip']

    @property
    def last_connection(self):
        # TODO confirm this does get set, or if the API documentation is wrong
        return self._data.get('last_connection')

    @property
    def error_code(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['error_code']

    @property
    def battery_level(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['battery_level']

    @property
    def battery_health(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['battery_health']

    @property
    def postal_code(self):
        return self.structure.postal_code
        # return self._data['postal_code']

    def _temp_key(self, key):
        return "%s_%s" % (key, self.temperature_scale.lower())

    def _round_temp(self, temp):
        if self.temperature_scale == 'C':
            return round(temp * 2) / 2
        else:
            # F goes to nearest degree
            return int(round(temp))

    @property
    def temperature_scale(self):
        return self._data.get('temperature_scale')

    @temperature_scale.setter
    def temperature_scale(self, value):
        self._set('devices/thermostats', {'temperature_scale': value.upper()})

    @property
    def is_locked(self):
        return self._data.get('is_locked')

    @property
    def locked_temperature(self):
        low = self._data.get(self._temp_key('locked_temp_min'))
        high = self._data.get(self._temp_key('locked_temp_max'))
        return LowHighTuple(low, high)

    @property
    def temperature(self):
        return self._data.get('current_temperature')

    @property
    def min_temperature(self):
        if self.is_locked:
            return self.locked_temperature[0]
        else:
            if self.temperature_scale == 'C':
                return MINIMUM_TEMPERATURE_C
            else:
                return MINIMUM_TEMPERATURE_F

    @property
    def max_temperature(self):
        if self.is_locked:
            return self.locked_temperature[1]
        else:
            if self.temperature_scale == 'C':
                return MAXIMUM_TEMPERATURE_C
            else:
                return MAXIMUM_TEMPERATURE_F

    @temperature.setter
    def temperature(self, value):
        self.target = value

    @property
    def target(self):
        if self.mode == 'heat-cool':
            low = self._data.get('target_temperature_low')
            high = self._data.get('target_temperature_high')
            return LowHighTuple(low, high)

        return self._data.get('target_temperature')

    @target.setter
    def target(self, value):
        data = {}

        if self.mode == 'heat-cool':
            rounded_low = self._round_temp(value[0])
            rounded_high = self._round_temp(value[1])

            data['target_temperature_low'] = rounded_low
            data['target_temperature_high'] = rounded_high
        else:
            rounded_temp = self._round_temp(value)
            data['target_temperature'] = rounded_temp

        self._set('devices/thermostats', data)

    @property
    def away_temperature(self):
        # see https://nestdevelopers.io/t/new-things-for-fall/226
        raise NotImplementedError(
            "Deprecated Nest API, use eco_temperature instead")

    @away_temperature.setter
    def away_temperature(self, value):
        # see https://nestdevelopers.io/t/new-things-for-fall/226
        raise NotImplementedError(
                "Deprecated Nest API, use eco_temperature instead")

    @property
    def eco_temperature(self):
        # use get, since eco_temperature isn't always filled out
        low = self._data.get(self._temp_key('eco_temperature_low'))
        high = self._data.get(self._temp_key('eco_temperature_high'))

        return LowHighTuple(low, high)

    @eco_temperature.setter
    def eco_temperature(self, value):
        low, high = value
        data = {}

        if low is not None:
            data[self._temp_key('eco_temperature_low')] = low

        if high is not None:
            data[self._temp_key('eco_temperature_high')] = high

        self._set('devices/thermostats', data)

    @property
    def can_heat(self):
        return self._data.get('can_heat')

    @property
    def can_cool(self):
        return self._data.get('can_cool')

    @property
    def has_humidifier(self):
        return self._data.get('has_humidifier')

    @property
    def has_dehumidifier(self):
        return self._data.get('has_dehumidifier')

    @property
    def has_fan(self):
        return self._data.get('has_fan')

    @property
    def has_hot_water_control(self):
        return self._data.get('has_hot_water_control')

    @property
    def hot_water_temperature(self):
        return self._data.get('hot_water_temperature')

    @property
    def hvac_state(self):
        return self._data.get('hvac_state')

    @property
    def eco(self):
        raise NotImplementedError("Deprecated Nest API")
        # eco_mode = self._data['eco']['mode']
        # # eco modes can be auto-eco or manual-eco
        # return eco_mode.endswith('eco')

    @eco.setter
    def eco(self, value):
        raise NotImplementedError("Deprecated Nest API")
        # data = {'eco': self._data['eco']}
        # if value:
        #     data['eco']['mode'] = 'manual-eco'
        # else:
        #     data['eco']['mode'] = 'schedule'
        # data['eco']['mode_update_timestamp'] = time.time()
        # self._set('device', data)

    @property
    def previous_mode(self):
        return self._data.get('previous_hvac_mode')

    @property
    def time_to_target(self):
        return self._data.get('time_to_target')

    @property
    def time_to_target_training(self):
        return self._data.get('time_to_target_training')


class SmokeCoAlarm(Device):
    @property
    def is_smoke_co_alarm(self):
        return True

    @property
    def _device(self):
        return self._data

    @property
    def auto_away(self):
        raise NotImplementedError("No longer available in Nest API.")
        # return self._data['auto_away']

    @property
    def battery_health(self):
        return self._data.get('battery_health')

    @property
    def battery_health_state(self):
        raise NotImplementedError("use battery_health instead")

    @property
    def battery_level(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['battery_level']

    @property
    def capability_level(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['capability_level']

    @property
    def certification_body(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['certification_body']

    @property
    def co_blame_duration(self):
        raise NotImplementedError("No longer available in Nest API")
        # if 'co_blame_duration' in self._data:
        #     return self._data['co_blame_duration']

    @property
    def co_blame_threshold(self):
        raise NotImplementedError("No longer available in Nest API")
        # if 'co_blame_threshold' in self._data:
        #     return self._data['co_blame_threshold']

    @property
    def co_previous_peak(self):
        raise NotImplementedError("No longer available in Nest API")
        # if 'co_previous_peak' in self._data:
        #     return self._data['co_previous_peak']

    @property
    def co_sequence_number(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['co_sequence_number']

    @property
    def co_status(self):
        # TODO deprecate for new name
        return self._data.get('co_alarm_state')

    @property
    def color_status(self):
        return self._data.get('ui_color_state')

    @property
    def component_als_test_passed(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['component_als_test_passed']

    @property
    def component_co_test_passed(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['component_co_test_passed']

    @property
    def component_heat_test_passed(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['component_heat_test_passed']

    @property
    def component_hum_test_passed(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['component_hum_test_passed']

    @property
    def component_led_test_passed(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['component_led_test_passed']

    @property
    def component_pir_test_passed(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['component_pir_test_passed']

    @property
    def component_smoke_test_passed(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['component_smoke_test_passed']

    @property
    def component_temp_test_passed(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['component_temp_test_passed']

    @property
    def component_us_test_passed(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['component_us_test_passed']

    @property
    def component_wifi_test_passed(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['component_wifi_test_passed']

    @property
    def creation_time(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['creation_time']

    @property
    def device_external_color(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['device_external_color']

    @property
    def device_locale(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['device_locale']

    @property
    def fabric_id(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['fabric_id']

    @property
    def factory_loaded_languages(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['factory_loaded_languages']

    @property
    def gesture_hush_enable(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['gesture_hush_enable']

    @property
    def heads_up_enable(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['heads_up_enable']

    @property
    def home_alarm_link_capable(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['home_alarm_link_capable']

    @property
    def home_alarm_link_connected(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['home_alarm_link_connected']

    @property
    def home_alarm_link_type(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['home_alarm_link_type']

    @property
    def hushed_state(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['hushed_state']

    @property
    def installed_locale(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['installed_locale']

    @property
    def kl_software_version(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['kl_software_version']

    @property
    def latest_manual_test_cancelled(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['latest_manual_test_cancelled']

    @property
    def latest_manual_test_end_utc_secs(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['latest_manual_test_end_utc_secs']

    @property
    def latest_manual_test_start_utc_secs(self):
        # TODO confirm units, deprecate for new method name
        return self._data.get('last_manual_test_time')

    @property
    def last_manual_test_time(self):
        # TODO parse time, check that it's in the dict
        return self._data.get('last_manual_test_time')

    @property
    def line_power_present(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['line_power_present']

    @property
    def night_light_continuous(self):
        raise NotImplementedError("No longer available in Nest API")
        # if 'night_light_continuous' in self._data:
        #     return self._data['night_light_continuous']

    @property
    def night_light_enable(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['night_light_enable']

    @property
    def ntp_green_led_enable(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['ntp_green_led_enable']

    @property
    def product_id(self):
        return self._data.get('product_id')

    @property
    def replace_by_date_utc_secs(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['replace_by_date_utc_secs']

    @property
    def resource_id(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['resource_id']

    @property
    def smoke_sequence_number(self):
        return self._data.get('smoke_sequence_number')

    @property
    def smoke_status(self):
        return self._data.get('smoke_alarm_state')

    @property
    def spoken_where_id(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['spoken_where_id']

    @property
    def steam_detection_enable(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['steam_detection_enable']

    @property
    def thread_mac_address(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['thread_mac_address']

    @property
    def wifi_ip_address(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['wifi_ip_address']

    @property
    def wifi_mac_address(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['wifi_mac_address']

    @property
    def wifi_regulatory_domain(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['wifi_regulatory_domain']

    @property
    def wired_led_enable(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['wired_led_enable']

    @property
    def wired_or_battery(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['wired_or_battery']

class Camera(Device):
    @classmethod
    def is_handler_for(self, type):
        return type == CAMERA

    @property
    def is_camera(self):
        return True

    @property
    def _device(self):
        return self._data
    
    @property
    def name(self):
        return self.where
    
    @property
    def name_long(self):
        return self.name

    @property
    def ongoing_event(self):
        if self.last_event is not None and self.last_event.is_ongoing:
            return self.last_event

    def has_ongoing_motion_in_zone(self, zone_id):
        if self.ongoing_event is not None:
            return self.last_event.has_ongoing_motion_in_zone(zone_id)
        return False

    @property
    def sound_detected(self):
        if self.ongoing_event is not None:
            return self.last_event.has_ongoing_sound()
        return False

    @property
    def motion_detected(self):
        if self.ongoing_event is not None:
            return self.last_event.has_ongoing_motion()
        return False

    @property
    def person_detected(self):
        if self.ongoing_event is not None:
            return self.last_event.has_ongoing_person()
        return False

    @property
    def activity_zones(self):
        return [ActivityZone(self, z['id'])
                for z in self._data.get('activity_zones', [])]

    @property
    def last_event(self):
        if 'last_event' in self._data:
            return CameraEvent(self)

    @property
    def is_streaming(self):
        return self._data.get('streaming_state') == 'streaming-enabled'
    
    @property
    def online(self):
        return self._data.get('streaming_state') == 'streaming-enabled'

    @is_streaming.setter
    def is_streaming(self, value):
        self._set('devices/cameras', {'is_streaming': value})

    @property
    def is_video_history_enabled(self):
        return self._data.get('is_video_history_enabled')

    @property
    def is_audio_enabled(self):
        return self._data.get('is_audio_input_enabled')

    @property
    def is_public_share_enabled(self):
        return self._data.get('is_public_share_enabled')

    @property
    def capabilities(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['capabilities']

    @property
    def cvr(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['cvr_enrolled']

    @property
    def nexustalk_host(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['direct_nexustalk_host']

    @property
    def download_host(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['download_host']

    @property
    def last_connected(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['last_connected_time']

    @property
    def last_cuepoint(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['last_cuepoint']

    @property
    def live_stream(self):
        # return self._data['live_stream_host']
        raise NotImplementedError("No longer available in Nest API")

    @property
    def mac_address(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['mac_address']

    @property
    def model(self):
        return self._data.get('model')

    @property
    def nexus_api_http_server_url(self):
        # return self._data['nexus_api_http_server_url']
        raise NotImplementedError("No longer available in Nest API")

    @property
    def streaming_state(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['streaming_state']

    @property
    def component_hum_test_passed(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['component_hum_test_passed']

    @property
    def component_led_test_passed(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['component_led_test_passed']

    @property
    def component_pir_test_passed(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['component_pir_test_passed']

    @property
    def component_smoke_test_passed(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['component_smoke_test_passed']

    @property
    def component_temp_test_passed(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['component_temp_test_passed']

    @property
    def component_us_test_passed(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['component_us_test_passed']

    @property
    def component_wifi_test_passed(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['component_wifi_test_passed']

    @property
    def creation_time(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['creation_time']

    @property
    def device_external_color(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['device_external_color']

    @property
    def device_locale(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['device_locale']

    @property
    def fabric_id(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['fabric_id']

    @property
    def factory_loaded_languages(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['factory_loaded_languages']

    @property
    def installed_locale(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['installed_locale']

    @property
    def kl_software_version(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['kl_software_version']

    @property
    def product_id(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['product_id']

    @property
    def resource_id(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['resource_id']

    @property
    def spoken_where_id(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['spoken_where_id']

    @property
    def thread_mac_address(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['thread_mac_address']

    @property
    def where_id(self):
        return self._data.get('where_id')

    @property
    def wifi_ip_address(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['wifi_ip_address']

    @property
    def wifi_mac_address(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['wifi_mac_address']

    @property
    def wifi_regulatory_domain(self):
        raise NotImplementedError("No longer available in Nest API")
        # return self._data['wifi_regulatory_domain']

    @property
    def snapshot_url(self):
        if ('snapshot_url' in self._data and
                self._data['snapshot_url'] != SIMULATOR_SNAPSHOT_URL):
            return self._data['snapshot_url']['snapshot_url_prefix'] + self._data['snapshot_url']['snapshot_url_suffix']
        else:
            return SIMULATOR_SNAPSHOT_PLACEHOLDER_URL

    @property
    def web_url(self):
        return self._data.get('web_url')

class Structure(NestBase):
    @classmethod
    def is_handler_for(self, type):
        return type == STRUCTURE
    
    def __repr__(self):
        return str(self._data)

    def _set_away(self, value, auto_away=False):
        self._set('structures', {'away': AWAY_MAP[value]})

    @property
    def away(self):
        return self._data.get('away')

    @away.setter
    def away(self, value):
        self._set_away(value)

    @property
    def country_code(self):
        return self._data.get('country_code')

    @property
    def devices(self):
        raise NotImplementedError("Use thermostats instead")

    @property
    def thermostats(self):
        #if THERMOSTATS in self._structure:
            #return [Thermostat(devid, self._nest_api)
                    #for devid in self._structure[THERMOSTATS]]
        #else:
        return []

    @property
    def protectdevices(self):
        raise NotImplementedError("Use smoke_co_alarms instead")

    @property
    def smoke_co_alarms(self):
        #if SMOKE_CO_ALARMS in self._structure:
            #return [SmokeCoAlarm(devid, self._nest_api)
                    #for devid in self._structure[SMOKE_CO_ALARMS]]
        #else:
        return []

    @property
    def cameradevices(self):
        raise NotImplementedError("Use cameras instead")

    @property
    def cameras(self):
        #if CAMERAS in self._structure:
            #return [Camera(devid, self._nest_api)
                    #for devid in self._structure[CAMERAS]]
        #else:
        return []

    @property
    def dr_reminder_enabled(self):
        raise NotImplementedError("Deprecated Nest API")
        # return self._structure['dr_reminder_enabled']

    @property
    def emergency_contact_description(self):
        raise NotImplementedError("Deprecated Nest API")
        # return self._structure['emergency_contact_description']

    @property
    def emergency_contact_type(self):
        raise NotImplementedError("Deprecated Nest API")
        # return self._structure['emergency_contact_type']

    @property
    def emergency_contact_phone(self):
        raise NotImplementedError("Deprecated Nest API")
        # return self._structure['emergency_contact_phone']

    @property
    def enhanced_auto_away_enabled(self):
        # FIXME there is probably an equivilant thing for this
        raise NotImplementedError("Deprecated Nest API")
        # return self._structure['topaz_enhanced_auto_away_enabled']

    @property
    def eta_preconditioning_active(self):
        # FIXME there is probably an equivilant thing for this
        # or something that can be recommended
        raise NotImplementedError("Deprecated Nest API")
        # return self._structure['eta_preconditioning_active']

    @property
    def house_type(self):
        raise NotImplementedError("Deprecated Nest API")
        # return self._structure['house_type']

    @property
    def hvac_safety_shutoff_enabled(self):
        raise NotImplementedError("Deprecated Nest API")
        # return self._structure['hvac_safety_shutoff_enabled']

    @property
    def name(self):
        return self._data['name']

    @name.setter
    def name(self, value):
        self._set('structures', {'name': value})

    @property
    def location(self):
        raise NotImplementedError("Deprecated Nest API")
        # return self._structure.get('location')

    @property
    def address(self):
        raise NotImplementedError("Deprecated Nest API")
        # return self._structure.get('street_address')

    @property
    def num_thermostats(self):
        #if THERMOSTATS in self._structure:
            #return len(self._structure[THERMOSTATS])
        #else:
        return 0

    @property
    def num_cameras(self):
        #if CAMERAS in self._structure:
            #return len(self._structure[CAMERAS])
        #else:
        return 0

    @property
    def num_smokecoalarms(self):
        #if SMOKE_CO_ALARMS in self._structure:
            #return len(self._structure[SMOKE_CO_ALARMS])
        #else:
        return 0

    @property
    def measurement_scale(self):
        raise NotImplementedError(
            "Deprecated Nest API, see temperature_scale on "
            "thermostats instead")
        # return self._structure['measurement_scale']

    @property
    def postal_code(self):
        # TODO check permissions if this is empty?
        return self._data.get('postal_code')

    @property
    def renovation_date(self):
        raise NotImplementedError("Deprecated Nest API")
        # return self._structure['renovation_date']

    @property
    def structure_area(self):
        raise NotImplementedError("Deprecated Nest API")
        # return self._structure['structure_area']

    @property
    def time_zone(self):
        return self._data.get('time_zone')

    @property
    def peak_period_start_time(self):
        if 'peak_period_start_time' in self._data:
            return parse_time(self._data['peak_period_start_time'])

    @property
    def peak_period_end_time(self):
        if 'peak_period_end_time' in self._data:
            return parse_time(self._data['peak_period_end_time'])

    @property
    def eta_begin(self):
        if 'eta_begin' in self._data:
            return parse_time(self._data['eta_begin'])

    def _set_eta(self, trip_id, eta_begin, eta_end):
        if self.num_thermostats == 0:
            raise ValueError("ETA can only be set or cancelled when a"
                             " thermostat is in the structure.")
        if trip_id is None:
            raise ValueError("trip_id must be not None")

        data = {'trip_id': trip_id,
                'estimated_arrival_window_begin': eta_begin,
                'estimated_arrival_window_end': eta_end}

        self._set('structures', {'eta': data})

    def set_eta(self, trip_id, eta_begin, eta_end=None):
        """
        Set estimated arrival winow, use same trip_id to update estimation.
        Nest may choose to ignore inaccurate estimation.
        See: https://developers.nest.com/documentation/cloud/away-guide
             #make_an_eta_write_call
        """
        if eta_begin is None:
            raise ValueError("eta_begin must be not None")
        if eta_end is None:
            eta_end = eta_begin + datetime.timedelta(minutes=1)

        self._set_eta(trip_id, eta_begin.isoformat(), eta_end.isoformat())

    def cancel_eta(self, trip_id):
        """
        Cancel estimated arrival winow.
        """
        eta_end = datetime.datetime.utcnow()
        self._set_eta(trip_id, int(0), eta_end.isoformat())

    @property
    def wheres(self):
        return self._data.get('wheres')

    @wheres.setter
    def wheres(self, value):
        self._set('where', {'wheres': value})

    def add_where(self, name, ident=None):
        name = name.lower()

        if name in self.wheres:
            return self.wheres[name]

        name = ' '.join([n.capitalize() for n in name.split()])
        wheres = copy.copy(self.wheres)

        if ident is None:
            ident = str(uuid.uuid4())

        wheres.append({'name': name, 'where_id': ident})
        self.wheres = wheres

        return self.add_where(name)

    def remove_where(self, name):
        name = name.lower()

        if name not in self.wheres:
            return None

        ident = self.wheres[name]

        wheres = [w for w in copy.copy(self.wheres)
                  if w['name'] != name and w['where_id'] != ident]

        self.wheres = wheres
        return ident

    @property
    def security_state(self):
        """
        Return 'ok' or 'deter'. Need sercurity state ready permission.
        Note: this is NOT for Net Secruity alarm system.
        See https://developers.nest.com/documentation/cloud/security-guide
        """
        return self._data.get('wwn_security_state')


class ActivityZone(NestBase):
    @classmethod
    def is_handler_for(self, type):
        return False

    def __init__(self, camera, zone_id):
        self.camera = camera
        super().__init__(self, camera.serial, camera._nest_api)
        # camera's activity_zone dict has int, but an event's list of
        # activity_zone ids is strings `\/0_0\/`
        self._zone_id = int(zone_id)

    @property
    def _camera(self):
        return self.camera._device

    @property
    def _repr_name(self):
        return self.name

    @property
    def _activity_zone(self):
        return next(
            z for z in self._camera.get('activity_zones')
            if z['id'] == self.zone_id)

    @property
    def zone_id(self):
        return self._zone_id

    @property
    def name(self):
        return self._activity_zone.get('name')


class CameraEvent(NestBase):
    @classmethod
    def is_handler_for(self, type):
        return False

    def __init__(self, camera):
        super().__init__(self, camera.serial, camera._nest_api)
        self.camera = camera

    @property
    def _camera(self):
        return self.camera._device

    @property
    def _event(self):
        return self._camera.get('last_event')

    def __str__(self):
        return '<%s>' % (self.__class__.__name__)

    def __repr__(self):
        return str(self._event)

    def activity_in_zone(self, zone_id):
        if 'activity_zone_ids' in self._event:
            return str(zone_id) in self._event['activity_zone_ids']
        return False

    @property
    def activity_zones(self):
        if 'activity_zone_ids' in self._event:
            return [ActivityZone(self, z)
                    for z in self._event['activity_zone_ids']]

    @property
    def animated_image_url(self):
        return self._event.get('animated_image_url')

    @property
    def app_url(self):
        return self._event.get('app_url')

    @property
    def has_motion(self):
        return self._event.get('has_motion')

    @property
    def has_person(self):
        return self._event.get('has_person')

    @property
    def has_sound(self):
        return self._event.get('has_sound')

    @property
    def image_url(self):
        return self._event.get('image_url')

    @property
    def start_time(self):
        if 'start_time' in self._event:
            return parse_time(self._event['start_time'])

    @property
    def end_time(self):
        if 'end_time' in self._event:
            end_time = parse_time(self._event['end_time'])
            if end_time:
                return end_time + datetime.timedelta(seconds=30)

    @property
    def urls_expire_time(self):
        if 'urls_expire_time' in self._event:
            return parse_time(self._event['urls_expire_time'])

    @property
    def web_url(self):
        return self._event.get('web_url')

    @property
    def is_ongoing(self):
        if self.end_time is not None:
            # sometimes, existing event is updated with a new start time
            # that's before the end_time which implies something new
            if self.start_time > self.end_time:
                return True

            now = datetime.datetime.now(self.end_time.tzinfo)
            # end time should be in the past
            return self.end_time > now
        # no end_time implies it's ongoing
        return True

    def has_ongoing_motion_in_zone(self, zone_id):
        if self.is_ongoing and self.has_motion:
            return self.activity_in_zone(zone_id)

    def has_ongoing_sound(self):
        if self.is_ongoing:
            return self.has_sound

    def has_ongoing_motion(self):
        if self.is_ongoing:
            return self.has_motion

    def has_ongoing_person(self):
        if self.is_ongoing:
            return self.has_person