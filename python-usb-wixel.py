#!/usr/bin/python

# Sokeriseuranta Box uploader

# This a "quick and dirty python script" which reads Dexcom G4 transmitter data
# from serial line and then uploads it to a REST API (Sokeriseuranta by default).

# This software is intended to be used with sokeriseuranta-mobile-wixel-xDrip Wixel 
# firmware and to be run on a Raspberry Pi.
# Get the Wixel code from https://github.com/mhaulo/sokeriseuranta-mobile-wixel-xDrip

# Wixel needs to have output printf like this, if connected via usb;
# printf("%lu %lu %lu %hhu %d %hhu %d \r\n", pPkt->src_addr,dex_num_decoder(pPkt->raw),dex_num_decoder(pPkt->filtered)*2, pPkt->battery, getPacketRSSI(pPkt),pPkt->txId,adcConvertToMillivolts(adcRead(0)));
#... Or this, if connected via GPIO:
# printf("%lu %hhu %d \r\n", dex_num_decoder(pPkt->raw), pPkt->battery, adcConvertToMillivolts(adcRead(0)));

# This script is based on code by jamorham (jarmoham.github.io)

import json
import logging
import socket
import sys
import time
import datetime
import os
from ConfigParser import SafeConfigParser
import platform
from urlparse import urlparse
import threading
import signal
import serial
from StringIO import StringIO
import re
import requests
import json

version = "0.1"

if platform.system() != "Windows":
	import grp
	DEFAULT_LOG_FILE = "/tmp/python-usb-wixel.log"
else:
	DEFAULT_LOG_FILE = "python-usb-wixel.log"


# Sokeriseuranta API info. These are read from a config file
api_endpoint = ""
api_token = ""
user_email = ""


# If you wired your wixel directly to the serial pins of the raspberry Pi set this to True
# for usb connected wixels leave it set as False

use_raspberry_pi_internal_serial_port = False

# Or you can store the configuration in a file which overrides whatever is set in this script

config = SafeConfigParser({'api_endpoint': api_endpoint,
						   'api_token': api_token,
						   'user_email': user_email,
						   'use_raspberry_pi_internal_serial_port': False,
						   'DEFAULT_LOG_FILE': DEFAULT_LOG_FILE})

# script should be python-usb-wixel.py and then config file will be python-usb-wixel.cfg
#config_path = re.sub(r".py$", ".cfg", os.path.realpath(__file__))
config_path="/home/pi/Documents/python-usb-wixel-xdrip/python-usb-wixel.cfg"

if (os.path.isfile(config_path)):
	config.read(config_path)
	logger.info("Loading configuration from: " + config_path)
	api_endpoint = config.get('main', 'api_endpoint').strip()
	user_email = config.get('main', 'user_email').strip()
	api_token = config.get('main', 'api_token').strip()
	
	try:
		use_raspberry_pi_internal_serial_port = config.getboolean('main', 'use_raspberry_pi_internal_serial_port')
	except:
		use_raspberry_pi_internal_serial_port = False
		
	DEFAULT_LOG_FILE = config.get('main', 'DEFAULT_LOG_FILE').strip()
else:
	print "Config file " + config_path + " not found, using default values"
	logger.info("Config file " + config_path + " not found, using default values")


# output template
mydata = {"TransmitterId": "0", "_id": 1, "CaptureDateTime": 0, "RelativeTime": 0, "ReceivedSignalStrength": 0,
		  "RawValue": 0, "TransmissionId": 0, "BatteryLife": 0, "UploadAttempts": 0, "Uploaded": 0,
		  "UploaderBatteryLife": 0, "FilteredValue": 0}


# threads

def serialThread(dummy):
	logger.info("entering serial loop - waiting for data from wixel")
	global mydata
	
	while 1:
		try:
			# sometimes the wixel reboots and comes back as a different
			# device - this code seemed to catch that happening
			# more complex code might be needed if the pi has other
			# ACM type devices.

			if os.path.exists("/dev/ttyACM0"):
				ser = serial.Serial('/dev/ttyACM0', 9600)
			else:
				if os.path.exists("/dev/ttyACM1"):
					ser = serial.Serial('/dev/ttyACM1', 9600)
				else:
					if use_raspberry_pi_internal_serial_port and os.path.exists("/dev/ttyAMA0"):
						ser = serial.Serial('/dev/ttyAMA0', 9600)
					else:
						logger.error("Could not find any serial device")
						time.sleep(30)

			try:
				serial_line = ser.readline()

				# debug print what we received
				print serial_line
				serial_line = re.sub("[^0-9 \n-]", "", serial_line)
				logger.info("Serial line: " + serial_line.strip())

				# simple space delimited data records
				datax = serial_line.split(" ")

				if datax[0] == "\n":
					print "Detected loss of serial sync - restarting"
					logger.warning("Serial line error: " + serial_line)
					logger.info("Detected loss of serial sync - restarting")
					break

				# update dictionary - no sanity checking here
				mydata['CaptureDateTime'] = str(int(time.time())) + "000"
				mydata['RelativeTime'] = "0"
				mydata['TransmitterId'] = datax[0]
				mydata['RawValue'] = datax[1]
				mydata['FilteredValue'] = datax[2]
				mydata['BatteryLife'] = datax[3]
				mydata['ReceivedSignalStrength'] = datax[4]
				mydata['TransmissionId'] = datax[5]

				upload_data()

			except Exception, e:
				print "Exception: ", e
				logger.exception("Exception: ", e)

		except serial.serialutil.SerialException, e:
			print "Serial exception ", e
			logger.exception("Serial exception ", e)
			time.sleep(1)

		try:
			ser.close()
		except Exception, e:
			print "Serial close exception", e
			logger.exception("Serial close exception", e)

		time.sleep(6)
		

def upload_data():
	global api_endpoint
	global mydata

	if api_endpoint != "":
		tries = 0
		max_tries = 2
		success = False
		
		while (success == False) and (tries < max_tries):
			tries = tries + 1
			buffer = StringIO()
			entry_date = datetime.datetime.fromtimestamp(time.time()).strftime('%Y-%m-%d %H:%M:%S')
			bg_value = str(raw_to_bg(mydata['RawValue'], mydata['FilteredValue']))
			sensor_raw = mydata['RawValue']
			
			print "Data at " + entry_date + ": "
			print "RawValue: " + mydata['RawValue']
			#print "FilteredValue: " + mydata['FilteredValue']
			#print "Transmitter ID: " + mydata['TransmitterId']
			#print "Battery life: " + mydata['BatteryLife']
			print "BG value: " + bg_value
			print "\n"
			
			data = '{"log_entries": [{"log_entry": {"date": "' + entry_date + '", "value": "' + bg_value + '", "sensor_raw": "' + sensor_raw + '", "entry_type": "sensor_bg" }}]}'
						
			print "Sending data:"
			print data
			
			headers = {'Content-Type': 'application/json', 'Accept': 'application/json', 'Accept-Charset': 'UTF-8', 'X-User-Email': user_email, 'X-Access-Token': api_token}
			response = requests.post(api_endpoint, data=data, headers=headers)

			print "RESPONSE:"
			print response.json()
			
			success = True
			
def raw_to_bg(raw_value, filtered_value):
	# This is NOT a real calculation, just for testing purposes
	raw = (int(raw_value) + int(filtered_value)) / 2
	return float(raw) / float(1250*18)

# threads end

# MAIN

# some init
if (platform.system() != "Windows"):
	if os.getuid() == 0:
		logger.info("Dropping root")
		os.setgid(1000)  # make sure this user is in the dialout group or setgid to dialout
		try:
			os.setgid(grp.getgrnam("dialout").gr_gid)
		except:
			logger.exception("Couldn't find the dialout group to use")

		os.setuid(1000)
		
		if os.getuid() == 0:
			logger.error("Cannot drop root - exit!")
			sys.exit()
		else:
			logger.info("Dropped to user: ", os.getuid())
			
logger = logging.getLogger('python-usb-wixel')
hdlr = logging.FileHandler(DEFAULT_LOG_FILE)
formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
hdlr.setFormatter(formatter)
logger.addHandler(hdlr)
logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.INFO)

logger.info("Starting up")
print "Sokeriseuranta Box - version " + version

# start a thread to listen for serial data
threading.Thread(target=serialThread, args=("",)).start()

# main busy loop
try:
	while(1):
		sleep(2)	

except KeyboardInterrupt:
	print "Shutting down"
	logger.info("Shutting down")
	os.kill(os.getpid(), signal.SIGKILL)
