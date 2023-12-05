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

It's also possible to install this application via 'pip' from Github and one 
can launch the application as follows:

    $ nohup sysmon > sysmon.out &

NOTE: This application is designed to display data on the Raspberry Pi Sense HAT which 
      has an 8x8 LED, and a joystick. We also support various display modes including 
      a screen-saver mode, support for 'settings.toml', and more.

Dependencies:
    - adafruit-io - only install if you have an account with Adafruit IO
    - speedtest-cli - used for internet speed tests
    - pyfiglet - used to print nice logo :-)
"""

import argparse
import time
import sys
import asyncio

# import random
# from random import randint

from pathlib import Path
from datetime import datetime
from collections import deque

from . import constants as const
from . import system_data as f451SystemData

import f451_common.common as f451Common
import f451_logger.logger as f451Logger
import f451_cloud.cloud as f451Cloud

import f451_sensehat.sensehat as f451SenseHat

from pyfiglet import Figlet

from rich.console import Console
from rich.progress import track, Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from Adafruit_IO import RequestError, ThrottlingError
import speedtest


# =========================================================
#          G L O B A L S   A N D   H E L P E R S
# =========================================================
APP_VERSION = "0.3.0"
APP_NAME = "f451 Labs piF451 - SysMon"
APP_NAME_SHORT = "SysMon"
APP_LOG = "f451-pif451-sysmon.log"      # Individual logs for devices with multiple apps
APP_SETTINGS = "settings.toml"          # Standard for all f451 Labs projects
APP_DIR = Path(__file__).parent         # Find dir for this app

APP_MIN_SPEEDTEST_WAIT = 60             # Minimum wait in sec bettween speed test checks 

APP_DISPLAY_MODES = {
    f451SenseHat.KWD_DISPLAY_MIN: const.MAX_DISPL, 
    f451SenseHat.KWD_DISPLAY_MAX: const.MIN_DISPL
}

# Load settings
CONFIG = f451Common.load_settings(APP_DIR.joinpath(APP_SETTINGS))

# Initialize device instance which includes all sensors
# and LED display on Sense HAT
SENSE_HAT = f451SenseHat.SenseHat(CONFIG)

# Initialize logger and IO cloud
LOGGER = f451Logger.Logger(CONFIG, LOGFILE=APP_LOG)
UPLOADER = f451Cloud.Cloud(CONFIG)

# Verify that feeds exist
try:
    FEED_DWNLD = UPLOADER.aio_feed_info(CONFIG.get(const.KWD_FEED_DWNLD, None))
    FEED_UPLD = UPLOADER.aio_feed_info(CONFIG.get(const.KWD_FEED_UPLD, None))
    FEED_PING = UPLOADER.aio_feed_info(CONFIG.get(const.KWD_FEED_PING, None))

except RequestError as e:
    LOGGER.log_error(f"Application terminated due to REQUEST ERROR: {e}")
    sys.exit(1)


# We use these timers to track when to upload data and/or set
# display to sleep mode. Normally we'd want them to be local vars 
# inside 'main()'. However, since we need them reset them in the 
# button actions, they need to be global.
timeUpdate = time.time()
displayUpdate = timeUpdate


# =========================================================
#              H E L P E R   F U N C T I O N S
# =========================================================
def init_progressbar(refreshRate=2):
    """Initialize new progress bar."""
    return Progress(                     
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        transient=True,
        refresh_per_second=refreshRate
    )


def make_logo(maxWidth, appName, appVer, default=None, center=True):
    """Create a fancy logo using pyFiglet

    This will create a fancy multi-line ASCII-ized logo
    using pyFiglet library and 'slant' font. We'll also
    add in the version number on the last row, and we'll
    center the logo if theres enough space.

    If there is not enough space for  abig logo, then we
    can return a default string instead.

    Args:
        maxWidth:
            'int' max length of any row in the logo (usually console width)
        appName:
            'str' application name to use for logo
        appVer:
            'str' application version number. We'll prefix it with a 'v'
        default:
            'str' default string if space is too tight
        center:
            'bool' if True, then we'll 'center' logo lines within available space

    Returns:
        'str' with logo. A multiline-logo will have '\n' embedded                    
    """
    logoFont = Figlet(font='slant')
    logoStrRaw = logoFont.renderText(appName)
    logoStrArr = logoStrRaw.splitlines()
    logoLen = max([len(s) for s in logoStrArr])

    result = default

    if logoLen < maxWidth:
        verStr = 'v' + appVer
        lastCharPos = logoStrArr[-2].rfind('/')
        deltaStrLen = len(logoStrArr[-2]) - lastCharPos if lastCharPos >= 0 else len(logoStrArr[-2]) - len(verStr)
        logoStrArr[-1] = logoStrArr[-1][:-(len(verStr) + deltaStrLen)] + verStr + (' ' * deltaStrLen)
        newLogo = [s.center(maxWidth, ' ') for s in logoStrArr] if center else logoStrArr
        result = "\n".join(newLogo)

    return result


def debug_config_info(cliArgs, console=None):
    """Print/log some basic debug info."""

    if console:
        console.rule("Config Settings", style="grey", align="center")
    else:    
        LOGGER.log_debug("-- Config Settings --")

    LOGGER.log_debug(f"DISPL ROT:   {SENSE_HAT.displRotation}")
    LOGGER.log_debug(f"DISPL MODE:  {SENSE_HAT.displMode}")
    LOGGER.log_debug(f"DISPL PROGR: {SENSE_HAT.displProgress}")
    LOGGER.log_debug(f"SLEEP TIME:  {SENSE_HAT.displSleepTime}")
    LOGGER.log_debug(f"SLEEP MODE:  {SENSE_HAT.displSleepMode}")
    LOGGER.log_debug(f"IO DEL:      {CONFIG.get(const.KWD_DELAY, const.DEF_DELAY)}")
    LOGGER.log_debug(f"IO WAIT:     {CONFIG.get(const.KWD_WAIT, const.DEF_WAIT)}")
    LOGGER.log_debug(f"IO THROTTLE: {CONFIG.get(const.KWD_THROTTLE, const.DEF_THROTTLE)}")

    # Display Raspberry Pi serial and Wi-Fi status
    LOGGER.log_debug(f"Raspberry Pi serial: {f451Common.get_RPI_serial_num()}")
    LOGGER.log_debug(f"Wi-Fi: {(f451Common.STATUS_YES if f451Common.check_wifi() else f451Common.STATUS_UNKNOWN)}")

    # Display CLI args
    LOGGER.log_debug(f"CLI Args:\n{cliArgs}")


def init_cli_parser():
    """Initialize CLI (ArgParse) parser.

    Initialize the ArgParse parser with the CLI 'arguments' and
    return a new parser instance.

    Returns:
        ArgParse parser instance
    """
    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        description=f"{APP_NAME} [v{APP_VERSION}] - collect internet speed test data using Speedtest CLI, and upload to Adafruit IO and/or Arduino Cloud.",
        epilog="NOTE: This application requires active accounts with corresponding cloud services.",
    )

    parser.add_argument(
        "-V",
        "--version",
        action="store_true",
        help="display script version number and exit",
    )
    parser.add_argument(
        "-d",
        "--debug", 
        action="store_true", 
        help="run script in debug mode"
    )
    parser.add_argument(
        "--cron",
        action="store_true",
        help="use when running as cron job - run script once and exit",
    )
    parser.add_argument(
        "--noDisplay",
        action="store_true",
        default=False,
        help="do not display output on LED",
    )
    parser.add_argument(
        "--progress",
        action="store_true",
        help="show upload progress bar on LED",
    )
    parser.add_argument(
        "--log",
        action="store",
        type=str,
        help="name of log file",
    )
    parser.add_argument(
        "--uploads",
        action="store",
        type=int,
        default=-1,
        help="number of uploads before exiting",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=False,
        help="show output to CLI stdout",
    )

    return parser


async def upload_speedtest_data(*args, **kwargs):
    """Send speedtest data to cloud services.
    
    This helper function parses and sends speedtest data to 
    Adafruit IO and/or Arduino Cloud.

    NOTE: This function will upload specific environment 
          data using the following keywords:

          'download' - download speed
          'upload'   - upload speed
          'ping'     - PING response time

    Args:
        args:
            User can provide single 'dict' with data
        kwargs:
            User can provide individual data points as key-value pairs
    """
    # We combine 'args' and 'kwargs' to allow users to provide a 'dict' with
    # all data points and/or individual data points (which could override 
    # values in the 'dict').
    data = {**args[0], **kwargs} if args and isinstance(args[0], dict) else kwargs

    sendQ = []

    # Send download speed data ?
    if data.get(const.KWD_DATA_DWNLD, None) is not None:
        sendQ.append(UPLOADER.aio_send_data(FEED_DWNLD.key, data.get(const.KWD_DATA_DWNLD)))

    # Send upload speed data ?
    if data.get(const.KWD_DATA_UPLD, None) is not None:
        sendQ.append(UPLOADER.aio_send_data(FEED_UPLD.key, data.get(const.KWD_DATA_UPLD)))

    # Send ping response data ?
    if data.get(const.KWD_DATA_PING, None) is not None:
        sendQ.append(UPLOADER.aio_send_data(FEED_PING.key, data.get(const.KWD_DATA_PING)))

    # deviceID = SENSE_HAT.get_ID(DEF_ID_PREFIX)

    await asyncio.gather(*sendQ)


def get_speed_test_data(client):
    """Run actual speed test

    Args:
        client:
            We need full app context client

    Returns:
        'dict' with all SpeedTest data
    """
    client.get_best_server()
    client.download()
    client.upload()
    data = client.results.dict()

    return data


def btn_up(event):
    """SenseHat Joystick UP event
    
    Rotate display by -90 degrees and reset screen blanking
    """
    global displayUpdate

    if event.action != f451SenseHat.BTN_RELEASE:
        SENSE_HAT.display_rotate(-1)
        displayUpdate = time.time()


def btn_down(event):
    """SenseHat Joystick DOWN event
    
    Rotate display by +90 degrees and reset screen blanking
    """
    global displayUpdate

    if event.action != f451SenseHat.BTN_RELEASE:
        SENSE_HAT.display_rotate(1)
        displayUpdate = time.time()


def btn_left(event):
    """SenseHat Joystick LEFT event
    
    Switch display mode by 1 mode and reset screen blanking
    """
    global displayUpdate

    if event.action != f451SenseHat.BTN_RELEASE:
        SENSE_HAT.update_display_mode(-1)
        displayUpdate = time.time()


def btn_right(event):
    """SenseHat Joystick RIGHT event
    
    Switch display mode by 1 mode and reset screen blanking
    """
    global displayUpdate

    if event.action != f451SenseHat.BTN_RELEASE:
        SENSE_HAT.update_display_mode(1)
        displayUpdate = time.time()


def btn_middle(event):
    """SenseHat Joystick MIDDLE (down) event
    
    Turn display on/off
    """
    global displayUpdate

    if event.action != f451SenseHat.BTN_RELEASE:
        # Wake up?
        if SENSE_HAT.displSleepMode:
            SENSE_HAT.update_sleep_mode(False)
            displayUpdate = time.time()
        else:    
            SENSE_HAT.update_sleep_mode(True)


APP_JOYSTICK_ACTIONS = {
    f451SenseHat.KWD_BTN_UP: btn_up,
    f451SenseHat.KWD_BTN_DWN: btn_down,
    f451SenseHat.KWD_BTN_LFT: btn_left,
    f451SenseHat.KWD_BTN_RHT: btn_right,
    f451SenseHat.KWD_BTN_MDL: btn_middle,
}


# =========================================================
#      M A I N   F U N C T I O N    /   A C T I O N S
# =========================================================
def main(cliArgs=None):
    """Main function.

    This function will goes through the setup and then runs the
    main application loop.

    NOTE:
     -  Application will exit with error level 1 if invalid Adafruit IO
        or Arduino Cloud feeds are provided

     -  Application will exit with error level 0 if either no arguments 
        are entered via CLI, or if arguments '-V' or '--version' are used. 
        No data will be uploaded will be sent in that case.

    Args:
        cliArgs:
            CLI arguments used to start application
    """
    global LOGGER
    global SENSE_HAT
    global timeUpdate
    global displayUpdate

    cli = init_cli_parser()
    console = Console()

    # Show 'help' and exit if no args
    cliArgs, unknown = cli.parse_known_args(cliArgs)
    if (not cliArgs and len(sys.argv) == 1):
        cli.print_help(sys.stdout)
        sys.exit(0)

    if cliArgs.version:
        print(f"{APP_NAME} (v{APP_VERSION})")
        sys.exit(0)

    # Display LOGO :-)
    conWidth, conHeight = console.size
    print(make_logo(
            conWidth, 
            APP_NAME_SHORT, 
            APP_VERSION, 
            f"{APP_NAME} (v{APP_VERSION})"
        )
    )

    # Initialize Sense HAT joystick and LED display
    SENSE_HAT.joystick_init(**APP_JOYSTICK_ACTIONS)
    SENSE_HAT.display_init(**APP_DISPLAY_MODES)
    SENSE_HAT.update_sleep_mode(cliArgs.noDisplay)

    if cliArgs.progress:
        SENSE_HAT.displProgress(True)

    # Get core settings
    ioFreq = CONFIG.get(const.KWD_FREQ, const.DEF_FREQ)
    ioDelay = CONFIG.get(const.KWD_DELAY, const.DEF_DELAY)
    ioWait = max(CONFIG.get(const.KWD_WAIT, const.DEF_WAIT), APP_MIN_SPEEDTEST_WAIT)
    ioThrottle = CONFIG.get(const.KWD_THROTTLE, const.DEF_THROTTLE)
    ioRounding = CONFIG.get(const.KWD_ROUNDING, const.DEF_ROUNDING)
    ioUploadAndExit = cliArgs.cron

    logLvl = CONFIG.get(f451Logger.KWD_LOG_LEVEL, f451Logger.LOG_NOTSET)
    debugMode = (logLvl == f451Logger.LOG_DEBUG)

    # Initialize core data queues and SpeedTest client
    systemData = f451SystemData.SystemData(1, SENSE_HAT.widthLED)
    stClient = speedtest.Speedtest(secure=True)

    # Update log file or level?
    if cliArgs.debug:
        LOGGER.set_log_level(f451Logger.LOG_DEBUG)
        logLvl = f451Logger.LOG_DEBUG
        debugMode = True

    if cliArgs.log is not None:
        LOGGER.set_log_file(logLvl, cliArgs.log)

    # -- Main application loop --
    timeSinceUpdate = 0
    timeUpdate = time.time()
    displayUpdate = timeUpdate
    uploadDelay = ioDelay       # Ensure that we do NOT upload first reading
    maxUploads = int(cliArgs.uploads)
    numUploads = 0
    exitNow = False

    if debugMode:
        debug_config_info(cliArgs, console)

    console.rule(style="grey", align="center")
    LOGGER.log_info("-- START Data Logging --")

    try:
        while not exitNow:
            timeCurrent = time.time()
            timeSinceUpdate = timeCurrent - timeUpdate
            SENSE_HAT.update_sleep_mode(
                (timeCurrent - displayUpdate) > SENSE_HAT.displSleepTime,
                cliArgs.noDisplay
            )

            # Let's get some Speedtest data ...
            with console.status("Get SpeedTest data ..."):
                speedData = get_speed_test_data(stClient)

            dwnld = round(speedData[const.KWD_DATA_DWNLD]/const.MBITS_PER_SEC, 1)
            upld = round(speedData[const.KWD_DATA_UPLD]/const.MBITS_PER_SEC, 1)
            ping = speedData[const.KWD_DATA_PING]

            # Is it time to upload data?
            if timeSinceUpdate >= uploadDelay:
                try:
                    asyncio.run(upload_speedtest_data(
                        download = round(dwnld, ioRounding), 
                        upload = round(upld, ioRounding), 
                        ping = round(ping, ioRounding), 
                        deviceID = f451Common.get_RPI_ID(f451Common.DEF_ID_PREFIX)
                    ))

                except RequestError as e:
                    LOGGER.log_error(f"Application terminated: {e}")
                    sys.exit(1)

                except ThrottlingError:
                    # Keep increasing 'ioDelay' each time we get a 'ThrottlingError'
                    uploadDelay += ioThrottle
                    
                else:
                    # Reset 'uploadDelay' back to normal 'ioFreq' on successful upload
                    numUploads += 1
                    uploadDelay = ioFreq
                    exitNow = (exitNow or ioUploadAndExit)
                    LOGGER.log_info(f"Uploaded: DWN: {round(dwnld, ioRounding)} - UP: {round(upld, ioRounding)} - PING: {round(ping, ioRounding)}")

                finally:
                    timeUpdate = timeCurrent
                    exitNow = ((maxUploads > 0) and (numUploads >= maxUploads))

            # Check display mode. Each mode corresponds to a data type
            if SENSE_HAT.displMode == const.DISPL_DWNLD:        # type = "download"
                systemData.download.data.append(dwnld)
                SENSE_HAT.display_as_graph(systemData.download.as_dict())

            elif SENSE_HAT.displMode == const.DISPL_UPLD:       # type = "upload"
                systemData.upload.data.append(upld)
                SENSE_HAT.display_as_graph(systemData.upload.as_dict())

            elif SENSE_HAT.displMode == const.DISPL_PING:       # type = "ping"
                systemData.ping.data.append(ping)
                SENSE_HAT.display_as_graph(systemData.ping.as_dict())
                    
            else:                                               # Display sparkles
                SENSE_HAT.display_sparkle()

            # Are we done?
            if not exitNow:
                # If not, then lets update the progress bar as needed, and then rest
                # a bit before we go through this whole loop all over again ... phew!
                cliProgress = init_progressbar()
                with cliProgress:
                    for _ in cliProgress.track(range(ioWait), description="Waiting for next speed test ..."):
                        SENSE_HAT.display_progress(timeSinceUpdate / uploadDelay)
                        time.sleep(1)

    except KeyboardInterrupt:
        exitNow = True

    # A bit of clean-up before we exit
    LOGGER.log_info("-- END Data Logging --")
    SENSE_HAT.display_reset()
    SENSE_HAT.display_off()
    
    now = datetime.now()
    console.rule("Summary", style="grey", align="center")
    console.print(f"{APP_NAME} (v{APP_VERSION})")
    console.print(f"Num uploads: {numUploads}")
    console.print(f"Date:        {now:%a %b %-d, %Y}")
    console.print(f"Time:        {now:%-I:%M:%S %p}")
    console.rule(style="grey", align="center")


# =========================================================
#            G L O B A L   C A T C H - A L L
# =========================================================
if __name__ == "__main__":
    main()  # pragma: no cover
