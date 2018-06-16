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

DEFAULT_LOG_FILE = "/home/pi/bin/sokeriseuranta-box-uploader.log"

def read_config():
	config_items = {'api_endpoint': '',
					'api_token': '',
					'user_email': '',
					'use_raspberry_pi_internal_serial_port': False,
					'DEFAULT_LOG_FILE': ''}
	
	config = SafeConfigParser({'api_endpoint': '',
							'api_token': '',
							'user_email': '',
							'use_raspberry_pi_internal_serial_port': False,
							'DEFAULT_LOG_FILE': DEFAULT_LOG_FILE})

	# script should be python-usb-wixel.py and then config file will be python-usb-wixel.cfg
	config_path = re.sub(r".py$", ".cfg", os.path.realpath(__file__))
	#config_path="/home/pi/Documents/python-usb-wixel-xdrip/python-usb-wixel.cfg"

	if (os.path.isfile(config_path)):
		config.read(config_path)
		print "Loading configuration from: " + config_path
		config_items['api_endpoint'] = config.get('main', 'api_endpoint').strip()
		config_items['user_email'] = config.get('main', 'user_email').strip()
		config_items['api_token'] = config.get('main', 'api_token').strip()
		
		try:
			config_items['use_raspberry_pi_internal_serial_port'] = config.getboolean('main', 'use_raspberry_pi_internal_serial_port')
		except:
			config_items['use_raspberry_pi_internal_serial_port'] = False
			
		config_items['DEFAULT_LOG_FILE'] = config.get('main', 'DEFAULT_LOG_FILE').strip()
	else:
		print "No custom config file: " + config_path
		
	return config_items


def parse_serial_data(input, datasource):
	output = {"TransmitterId": "0", "_id": 1, "CaptureDateTime": 0, "RelativeTime": 0, "ReceivedSignalStrength": 0,
	          "RawValue": 0, "TransmissionId": 0, "BatteryLife": 0, "UploadAttempts": 0, "Uploaded": 0,
	          "UploaderBatteryLife": 0, "FilteredValue": 0}
	
	try:
		if datasource == "usb":
			output['CaptureDateTime'] = str(int(time.time())) + "000"
			output['RelativeTime'] = "0"
			output['TransmitterId'] = input[0]
			output['RawValue'] = input[1]
			output['FilteredValue'] = input[2]
			output['BatteryLife'] = input[3]
			output['ReceivedSignalStrength'] = input[4]
			output['TransmissionId'] = input[5]
		elif datasource == "serial":
			# Parse data from serial line - only these values here, there's 
			# less data compared to the usb connected wixel
			output['RawValue'] = input[0]
			output['BatteryLife'] = input[1]
			output['ReceivedSignalStrength'] = input[2]
			
			# These are set just for compatibility reasons
			output['CaptureDateTime'] = str(int(time.time())) + "000"
			output['RelativeTime'] = "0"
			output['TransmitterId'] = "XXXXX"
			output['FilteredValue'] = "0"
			output['TransmissionId'] = "0"
		else:
			output = None
	except Exception:
		print("Bad data on parser")
		output = None
		
	return output


def read_wixel():
	datax = None
	
	try:
		# sometimes the wixel reboots and comes back as a different
		# device - this code seemed to catch that happening
		# more complex code might be needed if the pi has other
		# ACM type devices.
		if os.path.exists("/dev/ttyACM0"):
			ser = serial.Serial('/dev/ttyACM0', 9600)
		elif os.path.exists("/dev/ttyACM1"):
			ser = serial.Serial('/dev/ttyACM1', 9600)
		elif use_raspberry_pi_internal_serial_port and os.path.exists("/dev/ttyAMA0"):
			ser = serial.Serial('/dev/ttyAMA0', 9600)
		else:
			#logger.error("Could not find any serial device")
			print("Could not find any serial device")
			time.sleep(30)

		serial_line = ser.readline()

		# debug print what we received
		print("serial line data: " + serial_line)
		serial_line = re.sub("[^0-9 \n-]", "", serial_line)
		#logger.info("Serial line: " + serial_line.strip())
		

		# simple space delimited data records
		datax = serial_line.split(" ")

		if len(datax) < 3:
			#raise ValueError('Malformed serial line data')
			print("Malformed serial line data")
			return None

		if datax[0] == "\n":
			print "Detected loss of serial sync - returning"
			#logger.warning("Serial line error: " + serial_line)
			print("Serial line error: " + serial_line)
			return None

	except Exception, e:
		print "Exception: ", e
		return None

	except serial.serialutil.SerialException, e:
		print "Serial exception ", e
		time.sleep(1)
		return None

	try:
		ser.close()
	except Exception, e:
		print "Serial close exception", e
		
	return datax


def upload_data(mydata, api_endpoint, user_email, api_token):
	if api_endpoint != "":
		tries = 0
		max_tries = 2
		success = False
		
		try:
			while (success == False) and (tries <= max_tries):
				tries = tries + 1
				buffer = StringIO()
				entry_date = datetime.datetime.fromtimestamp(time.time()).strftime('%Y-%m-%d %H:%M:%S')
				bg_value = str(raw_to_bg(mydata['RawValue'], mydata['FilteredValue']))
				data = '{"log_entries": [{"log_entry": {"date": "' + entry_date + '", "value": "' + bg_value + '", "entry_type": "sensor_bg" }}]}'
							
				print "Sending data:"
				print data
				
				headers = {'Content-Type': 'application/json', 'Accept': 'application/json', 'Accept-Charset': 'UTF-8', 'X-User-Email': user_email, 'X-Access-Token': api_token}
				response = requests.post(api_endpoint, data=data, headers=headers)

				print "RESPONSE:"
				print response.json()
				
				success = True
		except Exception:
			print("Bad data")


def raw_to_bg(raw_value, filtered_value):
	# This is NOT a real calculation, just for testing purposes
	raw = (int(raw_value) + int(filtered_value)) / 2
	return float(raw) / float(1250*18)


def drop_root_privileges():
	if os.getuid() == 0:
		print "Dropping root"
		os.setgid(1000)  # make sure this user is in the dialout group or setgid to dialout
		try:
			os.setgid(grp.getgrnam("dialout").gr_gid)
		except:
			print "Couldn't find the dialout group to use"

		os.setuid(1000)
		print "Dropped to user: ", os.getuid()
		if os.getuid() == 0:
			print "Cannot drop root - exit!"
			sys.exit()


def init_logger(log_file):
	logger = logging.getLogger('python-usb-wixel')
	hdlr = logging.FileHandler(log_file)
	formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
	hdlr.setFormatter(formatter)
	logger.addHandler(hdlr)
	logger.addHandler(logging.StreamHandler())

	# choose your logging level as required
	logger.setLevel(logging.INFO)
	# logger.setLevel(logging.WARNING)
	
	return logger


def main():
	config_items = read_config()
	drop_root_privileges()
	logger = init_logger(DEFAULT_LOG_FILE)
	
	logger.info("Startup")
	version = "0.2"
	print "Sokeriseuranta Box - version " + version
	
	datatype = "usb"
	
	if config_items['use_raspberry_pi_internal_serial_port'] == True:
		datatype = "serial"

	try:
		print "entering serial loop - waiting for data from wixel"
		
		while True:
			data = read_wixel()
			data = parse_serial_data(data, datatype)
			
			if data is not None:
				upload_data(data, config_items['api_endpoint'], config_items['user_email'], config_items['api_token'])
				
			time.sleep(6)
			
		s.close()

	except KeyboardInterrupt:
		print "Shutting down"
		sys.exit(0)


if __name__ == "__main__":
	main()
