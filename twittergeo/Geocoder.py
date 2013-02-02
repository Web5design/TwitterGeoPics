__author__ = "Jonas Geduldig"
__date__ = "December 20, 2012"
__license__ = "MIT"

import datetime
import fridge
import math
import os
import pygeocoder
import socket
import time

SOCKET_TIMEOUT = 3 # seconds -- need to set a timeout or connection can hang indefinitely
THROTTLE_INCR = .1 # seconds -- the time by which to dynamically increase between successive requests
DEFAULT_CACHE_FILE = 'geocode.cache'

class Geocoder:
	"""Wrapper for pygeocoder with Twitter helper methods and Google Maps throttling and caching.
	   
	   Google has two geocoding limits: 
			1) About 2,500 requests per day
			2) About 10 requests per second
	   Geocode request results are cached to a local file.
	"""

	def __init__(self, cache_file=None):
		"""Zero counters and open cache file.
		
		Parameters
		----------
		cache_file : str
			File path for cache file.  File will get opened for append or created if not found.
			If cache_file is not supplied, the default file will be used.
		
		"""
		self.count_request = 0         # total number of geocode requests
		self.count_request_ok = 0      # total number of successful geocode requests
		self.count_nowhere = 0         # total number of tweets without geocode and without location
		self.count_has_geocode = 0     # total number of tweets with embedded lat,lng
		self.count_has_location = 0    # total number of tweets with geocode-able location in user profile
		
		self.quota_exceeded = False    # true when Google's geocode request quota is exceeded (2500 per day)
		self.quota_exceeded_at = None  # date and time when Google's geocode request was first exceeded
		
		self.retry_count = 0           # retry once to check if request rate should be throttled
		self.throttle = THROTTLE_INCR  # the throttle in seconds to wait between requests
		self.last_exec = None          # time updated at each geocode request
		
		if cache_file == None:
			path = os.path.dirname(__file__)
			cache_file = os.path.join(path, DEFAULT_CACHE_FILE)
			
		# cache is a persistent dict with place address as key and lat/lng and count as value
		self.cache = fridge.Fridge(cache_file)

	def _throttle(self):
		"""Wait an interval to not exceed rate limit.  Called before each geocode request.
		
		"""
		if self.retry_count == 1:
			# increase the throttle to respect rate limit
			self.retry_count = 2
			self.throttle += THROTTLE_INCR
		elif self.retry_count == 2:
			# increased throttle was sufficient
			self.retry_count = 0
		now = datetime.datetime.now()
		if self.last_exec:
			# throttle for rate limit
			delta = self.throttle - (now - self.last_exec).total_seconds()
			if delta > 0:
				time.sleep(delta)
		self.last_exec = now

	def _should_retry(self):
		"""Handle an OVER QUERY LIMIT exception.  Called when GeocodeError is thrown.
		
		Return
		------
		retry : boolean
			True means wait 2 seconds, increase the throttle, and retry the request.
			False means stop making geocode requests because daily limit was exceeded.
			
		"""
		if not self.quota_exceeded:
			if self.retry_count == 0:
				# wait and retry once to see if we exceeded the rate limit 
				self.retry_count = 1
				time.sleep(2)
				return True
			else:
				# if the second attempt failed we exceeded the 24-hour quota
				self.retry_count = 0
				self.quota_exceeded = True
				self.quota_exceeded_at = datetime.datetime.now()
				return False
		else:
			return False

	def geocode(self, place):
		"""Returns Google's geocode data for a place.
		
		Parameters
		----------
		place : str
			An address or partial address in any format.
			
		Return
		------
		geocode data : dict
			Keys and values are from Google's JSON data.
		
		Raises
		------
		pygeocoder.GeocoderError
			Quota exceeded, indecipherable address, etc.
		Exception
			Socket errors.
			
		"""
		self._throttle()
		try:
			self.count_request += 1
			socket.setdefaulttimeout(SOCKET_TIMEOUT) 
			data = pygeocoder.Geocoder.geocode(place)
			self.count_request_ok += 1
			return data
		except pygeocoder.GeocoderError, e:
			if e.status == pygeocoder.GeocoderError.G_GEO_OVER_QUERY_LIMIT and self._should_retry():
				return self.geocode(place)
			else:
				raise
			
	def latlng_to_address(self, lat, lng):
		self._throttle()
		try:
			self.count_request += 1
			socket.setdefaulttimeout(SOCKET_TIMEOUT) 
			place = pygeocoder.Geocoder.latlng_to_address(lat, lng)
			self.count_request_ok += 1
			return place
		except pygeocoder.GeocoderError, e:
			if e.status == pygeocoder.GeocoderError.G_GEO_OVER_QUERY_LIMIT and self._should_retry():
				return self.latlng_to_address(lan, lng)
			else:
				raise
			
	def address_to_latlng(self, place):
		self._throttle()			
		try:
			self.count_request += 1
			socket.setdefaulttimeout(SOCKET_TIMEOUT) 
			lat, lng = pygeocoder.Geocoder.address_to_latlng(place)
			self.count_request_ok += 1
			return lat, lng
		except pygeocoder.GeocoderError, e:
			if e.status == pygeocoder.GeocoderError.G_GEO_OVER_QUERY_LIMIT and self._should_retry():
				return self.address_to_latlng(place)
			else:
				raise

	def geocode_tweet(self, status):
		"""Returns an address and coordinates associated with a tweet.
		
		Parameters
		----------
		status : dict
			Keys and values of a tweet (i.e. a Twitter status).
			
		Return
		------
		place : str
			An address or part of an address from either the tweeter's Twitter profile
			or from reverse geocoding coordinates associated with the tweet.
		latitude, longitude : float
			Coordinates either assocatiated with the tweet or from geocoding the 
			location in the tweeter's Twitter profile.
		
		Raises
		------
		See Geocoder.geocode() documentation.
			
		"""
		# start off with the location in the user's profile (it may be empty)
		place = status['user']['location']
		if status['coordinates'] != None:
			# the status is geocoded (swapped lat/lng), so use the coordinates to get the address
			lng, lat = status['coordinates']['coordinates']
			place = self.latlng_to_address(float(lat), float(lng))
			self.count_has_geocode += 1
		elif ':' in place:	
			# users may put their coordinates in their profile
			# the format is either "iPhone: lat,lng" or "UT: lat,lng"
			(tmp, coord) = place.split(':', 1)
			coord = coord.strip()
			if ',' in coord:
				lat, lng = coord.strip().split(',', 1)
			elif ' ' in coord:
				lat, lng = coord.strip().split(' ', 1)
			if lat != None and lng != None:
				try:
					lat, lng = lat.strip(), lng.strip()
					place = self.latlng_to_address(float(lat), float(lng))
					self.count_has_location += 1
				except ValueError, TypeError:
					pass
		elif place != None and place != '':
			# there is a location in the user profile, so see if it is usable
			# cache key is the place stripped of all punctuation and lower case
			key = ' '.join(''.join(e for e in place if e.isalnum() or e == ' ').split()).lower()
			cached_data = None
			if self.cache != None and key in self.cache:
				# see if the place name is in our cache
				cached_data = self.cache[key]
				lat, lng = cached_data[0], cached_data[1]
				cached_data[2] += 1
			if not cached_data:
				# see if Google can interpret the location
				lat, lng = self.address_to_latlng(place)
				cached_data = ( lat, lng, 1 )
			if self.cache != None:
				self.cache[key] = cached_data
			self.count_has_location += 1	
		else:
			lat, lng = None, None
			self.count_nowhere += 1
 		return place, lat, lng
					
	def get_region_box(self, place):
		"""Get the coordinates of a place and its bounding box.
		   The size of bounding box that Google returns depends on whether the place is
		   an address, a town or a country.
		
		Parameters
		----------
		place : str
			An address or partial address in any format.  Googles will try anything.

		Return
		------
		latitude, longitude : float
			The place's coordinates.
		latitude, longitude : float
			The place's SW coordinates.
		latitude, longitude : float
			The place's NE coordinates.
		
		Raises
		------
		See Geocoder.geocode() documentation.
		
		"""
		results = self.geocode(place)
		geometry = results.raw[0]['geometry']
		latC, lngC = geometry['location']['lat'], geometry['location']['lng']
		latSW, lngSW = geometry['bounds']['southwest']['lat'], geometry['bounds']['southwest']['lng']
		latNE, lngNE = geometry['bounds']['northeast']['lat'], geometry['bounds']['northeast']['lng'] 
		return latC, lngC, latSW, lngSW, latNE, lngNE
		
	def get_region_circle(self, place):
		"""Get the coordinates of a place and its bounding circle.
		   The circle's radius is calculated from Google's bounding box and the
		   Haversine formula that takes into account the curvature of the earch.
		   The motivation for this method is Twitter's Search API's 'geocode'
		   parameter.
		
		Parameters
		----------
		place : str
			An address or partial address in any format.

		Return
		------
		latitude, longitude : float
			The place's coordinates.
		radius : str
			Half the distance spanning the corner's of the place's bounding box in kilomters.
		
		Raises
		------
		See Geocoder.geocode() documentation.
		
		"""
		latC, lngC, latSW, lngSW, latNE, lngNE = self.get_region_box(place)
		D = self.distance(latSW, lngSW, latNE, lngNE)
		return latC, lngC, D/2

	@classmethod
	def distance(cls, lat1, lng1, lat2, lng2):
		"""Calculates the distance between two points on a sphere
		
		"""
		# Haversine distance formula
		lat1, lng1 = math.radians(lat1), math.radians(lng1)
		lat2, lng2 = math.radians(lat2), math.radians(lng2)
		s = math.sin((lat1-lat2)/2)
		t = math.sin((lng1-lng2)/2)
		a = s*s + math.cos(lat2)*math.cos(lat1)*t*t
		c = 2*math.atan2(math.sqrt(a), math.sqrt(1-a))
		earth_radius = 6371 # kilometers
		return earth_radius*c

	def print_stats(self):
		print '\n--STATS--'
		print 'geo requests:      ', self.count_request
		print 'geo requets ok:    ', self.count_request_ok
		print 'geo quota exceeded:', self.quota_exceeded_at
		print 'geo throttle:      ', self.throttle
		print 'has none:          ', self.count_nowhere
		print 'has geocode:       ', self.count_has_geocode
		print 'has location:      ', self.count_has_location

		if self.cache:
			counts = [ 0, 0, 0 ]
			max_place = ( None, 0 )
			for item in self.cache:
				count = self.cache[item][2]
				if count <= 5:
					counts[0] += 1
				elif count <= 10:
					counts[1] += 1
				else:
					counts[2] += 1
				if count > max_place[1]:
					max_place = ( item, count )
			print '\n--CACHE--'
			print 'size:              ', len(self.cache)
			print 'counts:            ', counts
			print 'max place:         ', max_place