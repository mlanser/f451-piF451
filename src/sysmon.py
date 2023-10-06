#!/usr/bin/env python3
"""f451 Labs SysMon application on piF451 device.

This application is designed for the f451 Labs piF451 device which is also equipped with 
a SenseHat add-on. The object is to continously read environment data (e.g. temperature, 
barometric pressure, and humidity from the SenseHat sensors and then upload the data to 
the Adafruit IO service.

To launch this application from terminal:

    $ nohup python -u sysmon.py > sysmon.out &

This command launches the 'sysmon' application in the background. The application will 
keep running even after the terminal window is closed. Any output will be redirected to 
the 'sysmon.out' file.    
"""

import time
import sys
import asyncio
import signal

from collections import deque
from random import randint
from pathlib import Path

from Adafruit_IO import RequestError, ThrottlingError

import constants as const
from pired import Device
from common import exit_now, EXIT_NOW

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


# =========================================================
#          G L O B A L S   A N D   H E L P E R S
# =========================================================
#         - 0    1    2    3    4    5    6    7 -
EMPTY_Q = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
COLORS  = [const.RGB_BLUE, const.RGB_GREEN, const.RGB_YELLOW, const.RGB_RED]

LOGLVL = "ERROR"
LOGFILE = "f451-piF451.log"
LOGNAME = "f451-piF451"

def debug_config_info(dev):
    dev.log_debug("-- Config Settings --")
    dev.log_debug(f"DISPL ROT:   {dev.displRotation}")
    dev.log_debug(f"DISPL MODE:  {dev.displMode}")
    dev.log_debug(f"DISPL PROGR: {dev.displProgress}")
    dev.log_debug(f"DISPL SLEEP: {dev.displSleep}")
    dev.log_debug(f"SLEEP CNTR:  {dev.sleepCounter}")
    dev.log_debug(f"IO DEL:      {dev.get_config(const.KWD_DELAY, const.DEF_DELAY)}")
    dev.log_debug(f"IO WAIT:     {dev.get_config(const.KWD_WAIT, const.DEF_WAIT)}")
    dev.log_debug(f"IO THROTTLE: {dev.get_config(const.KWD_THROTTLE, const.DEF_THROTTLE)}")


# =========================================================
#              H E L P E R   F U N C T I O N S
# =========================================================
async def send_all_sensor_data(client, tempsData, pressData, humidData):
    """
    Send sensor data to Adafruit IO

    Args:
        client:
            We need full app context client
        tempsData:
            'dict' with 'temperature feed' key and temperature data point
        pressData:
            'dict' with 'pressure feed' key and pressure data point
        humidData:
            'dict' with 'humidity feed' key and humidity data point

    Raises:
        RequestError:
            When API request fails
        ThrottlingError:
            When exceeding Adafruit IO rate limit
    """
    await asyncio.gather(
        client.send_sensor_data(tempsData),
        client.send_sensor_data(pressData),
        client.send_sensor_data(humidData)
    )


# =========================================================
#      M A I N   F U N C T I O N    /   A C T I O N S
# =========================================================
if __name__ == '__main__':
    # Init signals
    signal.signal(signal.SIGINT, exit_now)
    signal.signal(signal.SIGTERM, exit_now)

    # Get app dir
    appDir = Path(__file__).parent

    # Initialize TOML parser and load 'settings.toml' file
    try:
        with open(appDir.joinpath("settings.toml"), mode="rb") as fp:
            config = tomllib.load(fp)
    except tomllib.TOMLDecodeError:
        sys.exit("Invalid 'settings.toml' file")      

    # Initialize core data queues
    tempsQ = deque(EMPTY_Q, maxlen=const.LED_MAX_COL) # Temperature queue
    pressQ = deque(EMPTY_Q, maxlen=const.LED_MAX_COL) # Pressure queue
    humidQ = deque(EMPTY_Q, maxlen=const.LED_MAX_COL) # Humidity queue

    # Initialize device instance which includes the logger, 
    # SenseHat, and Adafruit IO client
    piF451 = Device(config, appDir)

    try:
        tempsFeed = piF451.get_feed_info(const.KWD_FEED_TEMPS)
        pressFeed = piF451.get_feed_info(const.KWD_FEED_PRESS)
        humidFeed = piF451.get_feed_info(const.KWD_FEED_HUMID)

    except RequestError as e:
        piF451.log_error(f"Application terminated due to REQUEST ERROR: {e}")
        piF451.reset_LED()
        sys.exit(1)

    # -- Main application loop --
    # Get core settings
    ioDelay = piF451.get_config(const.KWD_DELAY, const.DEF_DELAY)
    ioWait = piF451.get_config(const.KWD_WAIT, const.DEF_WAIT)
    ioThrottle = piF451.get_config(const.KWD_THROTTLE, const.DEF_THROTTLE)
    
    delayCounter = maxDelay = ioDelay       # Ensure that we upload first reading
    piF451.sleepCounter = piF451.displSleep   # Reset counter for screen blanking

    debug_config_info(piF451)
    piF451.log_info("-- START Data Logging --")
    while not EXIT_NOW:
        # We check the sensors each time we loop through ...
        tempC, press, humid = piF451.get_sensor_data()

        # ... and add the data to the queues
        tempsQ.append(tempC)
        pressQ.append(press)
        humidQ.append(humid)

        # Check 'sleepCounter' before we display anything
        if piF451.sleepCounter == 1:
            piF451.blank_LED()       # Need to blank screen once
        elif piF451.sleepCounter > 1:
            if piF451.displMode == const.DISPL_TEMP:
                piF451.update_LED(tempsQ, const.MIN_TEMP, const.MAX_TEMP)
            elif piF451.displMode == const.DISPL_PRESS:    
                piF451.update_LED(pressQ, const.MIN_PRESS, const.MAX_PRESS)
            elif piF451.displMode == const.DISPL_HUMID:    
                piF451.update_LED(humidQ, const.MIN_HUMID, const.MAX_HUMID)
            elif piF451.displMode == const.DISPL_SPARKLE:    
                piF451.sparkle_LED()
            else:    
                piF451.blank_LED()

            if piF451.displProgress:
                piF451.update_LED_progress(delayCounter, maxDelay)    

        # Update sleep counter for screen blanking as needed
        if piF451.sleepCounter > 0:    
            piF451.sleepCounter -= 1

        # Is it time to upload data?
        if delayCounter < maxDelay:
            delayCounter += 1       # We send data at set intervals
        else:
            try:
                asyncio.run(send_all_sensor_data(
                    piF451,
                    {"data": tempC, "feed": tempsFeed},
                    {"data": press, "feed": pressFeed},
                    {"data": humid, "feed": humidFeed},
                ))

            except RequestError as e:
                piF451.log_error(f"Application terminated due to REQUEST ERROR: {e}")
                raise

            except ThrottlingError as e:
                # Keep increasing 'maxDelay' each time we get a 'ThrottlingError'
                maxDelay += ioThrottle
                
            else:
                # Reset 'maxDelay' back to normal 'ioDelay' on successful upload
                maxDelay = ioDelay
                piF451.log_info(f"Uploaded: TEMP: {tempC} - PRESS: {press} - HUMID: {humid}")

            finally:
                # Reset counter even on failure
                delayCounter = 1

        # Let's rest a bit before we go through the loop again
        time.sleep(ioWait)

    # A bit of clean-up before we exit
    piF451.log_info("-- END Data Logging --")
    piF451.reset_LED()
