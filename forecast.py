import openmeteo_requests
import requests_cache
import pandas as pd
from retry_requests import retry


def get_weather_forecast(days: int):
	cache_session = requests_cache.CachedSession('.cache', expire_after=3600)
	retry_session = retry(cache_session, retries=5, backoff_factor=0.2)
	openmeteo = openmeteo_requests.Client(session=retry_session)

	url = "https://api.open-meteo.com/v1/forecast"
	params = {
		"latitude": 43.5789,
		"longitude": -79.6583,
		"daily": ["precipitation_probability_max", "temperature_2m_max", "temperature_2m_min", "weather_code"],
		"timezone": "America/New_York",
		"forecast_days": days + 1
	}
	responses = openmeteo.weather_api(url, params=params)

	# process first location
	response = responses[0]
	daily = response.Daily()
	daily_precipitation_probability_max = daily.Variables(0).ValuesAsNumpy()
	daily_temperature_2m_max = daily.Variables(1).ValuesAsNumpy()
	daily_temperature_2m_min = daily.Variables(2).ValuesAsNumpy()
	daily_weather_code = daily.Variables(3).ValuesAsNumpy()
	daily_data = {
		"date": pd.date_range(
					start=pd.to_datetime(daily.Time(), unit="s", utc=True),
					end=pd.to_datetime(daily.TimeEnd(), unit="s", utc=True),
					freq=pd.Timedelta(seconds=daily.Interval()),
					inclusive="left"),
		"precipitation_probability_max": daily_precipitation_probability_max,
		"temperature_2m_max": daily_temperature_2m_max,
		"temperature_2m_min": daily_temperature_2m_min,
		"weather_code": daily_weather_code
	}

	daily_dataframe = pd.DataFrame(data=daily_data)
	day_after = daily_dataframe.iloc[days]
	print(day_after)

	return day_after


forecast = get_weather_forecast(1)
precipitation_probability_max = forecast.iloc[1]
temperature_2m_max = forecast.iloc[2]
temperature_2m_min = forecast.iloc[3]
weather_code = forecast.iloc[4]

print(precipitation_probability_max)
print(round(temperature_2m_max))
print(round(temperature_2m_min))
print(round(weather_code))
