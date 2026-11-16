"""Weather from Open-Meteo (no key needed), fetched off the window's thread."""

from __future__ import annotations

import json
import threading
import urllib.parse
import urllib.request

from PySide6.QtCore import QObject, Signal

FORECAST = 'https://api.open-meteo.com/v1/forecast'
PLACES = 'https://geocoding-api.open-meteo.com/v1/search'

CONDITIONS = [
    ((0,), 'Clear', 'sun'), ((1,), 'Mainly clear', 'sun'), ((2,), 'Partly cloudy', 'cloud'),
    ((3,), 'Overcast', 'cloud'), ((45, 48), 'Fog', 'cloud'), ((51, 53, 55, 56, 57), 'Drizzle', 'rain'),
    ((61, 63, 65, 66, 67), 'Rain', 'rain'), ((71, 73, 75, 77), 'Snow', 'snow'),
    ((80, 81, 82), 'Rain showers', 'rain'), ((85, 86), 'Snow showers', 'snow'),
    ((95, 96, 99), 'Thunderstorm', 'storm'),
]


def condition(code: int | None) -> tuple[str, str]:
    for codes, words, kind in CONDITIONS:
        if code in codes:
            return words, kind
    return 'Unknown', 'cloud'


def _get(url: str, query: dict) -> dict:
    request = urllib.request.Request(f'{url}?{urllib.parse.urlencode(query)}',
                                     headers={'User-Agent': 'Dockline'})
    with urllib.request.urlopen(request, timeout=10) as answer:
        return json.loads(answer.read().decode('utf-8'))


def find_place(name: str) -> dict | None:
    """The best match for a city name: name, country, latitude, longitude."""
    data = _get(PLACES, {'name': name, 'count': 1, 'format': 'json'})
    results = data.get('results') or []
    if not results:
        return None
    place = results[0]
    return {'name': place.get('name', name), 'country': place.get('country', ''),
            'latitude': place['latitude'], 'longitude': place['longitude']}


class Weather(QObject):
    changed = Signal(dict)
    failed = Signal(str)

    def __init__(self):
        super().__init__()
        self.latest: dict | None = None
        self._busy = False

    def fetch(self, latitude: float, longitude: float, fahrenheit: bool) -> None:
        if self._busy:
            return
        self._busy = True
        threading.Thread(target=self._fetch, args=(latitude, longitude, fahrenheit), daemon=True).start()

    def _fetch(self, latitude, longitude, fahrenheit) -> None:
        try:
            data = _get(FORECAST, {
                'latitude': latitude, 'longitude': longitude, 'timezone': 'auto', 'forecast_days': 4,
                'current': 'temperature_2m,weather_code,wind_speed_10m,relative_humidity_2m',
                'daily': 'weather_code,temperature_2m_max,temperature_2m_min',
                'temperature_unit': 'fahrenheit' if fahrenheit else 'celsius',
            })
            current = data['current']
            daily = data['daily']
            result = {
                'temperature': current['temperature_2m'], 'code': current['weather_code'],
                'wind': current.get('wind_speed_10m'), 'humidity': current.get('relative_humidity_2m'),
                'days': [{'date': daily['time'][i], 'code': daily['weather_code'][i],
                          'high': daily['temperature_2m_max'][i], 'low': daily['temperature_2m_min'][i]}
                         for i in range(len(daily['time']))],
            }
            self.latest = result
            self.changed.emit(result)
        except Exception as error:             # network down, service changed: say so, keep the last
            self.failed.emit(str(error))
        finally:
            self._busy = False
