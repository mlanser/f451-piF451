#!/usr/bin/env python3
"""f451 Labs Demo application for piF451 device.

This application is designed demo core features of the f451 Labs piF451 device which is 
also equipped with a Sense HAT add-on. A secondary objective is to verify that the device
is configured properly and that the Sense HAT works.

To launch this application from terminal:

    $ python -m f451_pif451

It's also possible to install this package via 'pip' from Github and one can then launch 
this application as follows:

    $ f451_pif451

NOTE: This application is designed to display data on the Raspberry Pi Sense HAT which 
      has an 8x8 LED, and a joystick. We also support various display modes including 
      a screen-saver mode, support for 'settings.toml', and more.

NOTE: This application will NOT upload any data to the cloud.

TODO:
    - add more 8x8 images
    - add ability to pull data from Adafruit IO 'random feed'
"""

import argparse
import time
import sys
import asyncio
import platform
import random


from collections import deque, namedtuple
from datetime import datetime
from pathlib import Path

from . import constants as const
from . import demo_data as f451DemoData

import f451_common.common as f451Common
import f451_logger.logger as f451Logger

import f451_sensehat.sensehat as f451SenseHat
import f451_sensehat.sensehat_data as f451SenseData

from rich.console import Console
from rich.progress import Progress, TextColumn, BarColumn, TaskProgressColumn

from Adafruit_IO import RequestError, ThrottlingError

# Install Rich 'traceback' and 'pprint' to 
# make (debug) life is easier. Trust me!
from rich.pretty import pprint
from rich.traceback import install as install_rich_traceback
install_rich_traceback(show_locals=True)


# fmt: off
# =========================================================
#          G L O B A L S   A N D   H E L P E R S
# =========================================================
APP_VERSION = '0.0.0'
APP_NAME = 'f451 Labs - piF451 Demo'
APP_NAME_SHORT = 'Demo'
APP_HOST = platform.node()          # Get device 'hostname'
APP_LOG = 'f451-piF451-demo.log'    # Individual logs for devices with multiple apps
APP_SETTINGS = 'settings.toml'      # Standard for all f451 Labs projects
APP_DIR = Path(__file__).parent     # Find dir for this app

APP_MIN_SENSOR_READ_WAIT = 1        # Min wait in sec between sensor reads
APP_MIN_PROG_WAIT = 1               # Remaining min (loop) wait time to display prog bar
APP_WAIT_1SEC = 1
APP_MAX_DATA = 120                  # Max number of data points in the queue
APP_DELTA_FACTOR = 0.02             # Any change within X% is considered negligable

APP_DATA_TYPES = ['number1', 'number2']

APP_DISPLAY_MODES = {
    f451SenseHat.KWD_DISPLAY_MIN: const.MIN_DISPL,
    f451SenseHat.KWD_DISPLAY_MAX: 2,
}

# Load settings
CONFIG = f451Common.load_settings(APP_DIR.joinpath(APP_SETTINGS))

# Initialize device instance which includes all sensors
# and LED display on Sense HAT
SENSE_HAT = f451SenseHat.SenseHat(CONFIG)

# Initialize logger and IO cloud
LOGGER = f451Logger.Logger(CONFIG, LOGFILE=APP_LOG)

# Verify that feeds exist
# try:
#     FEED_DWNLD = UPLOADER.aio_feed_info(CONFIG.get(const.KWD_FEED_DWNLD, None))
#     FEED_UPLD = UPLOADER.aio_feed_info(CONFIG.get(const.KWD_FEED_UPLD, None))
#     FEED_PING = UPLOADER.aio_feed_info(CONFIG.get(const.KWD_FEED_PING, None))

# except RequestError as e:
#     LOGGER.log_error(f"Application terminated due to REQUEST ERROR: {e}")
#     sys.exit(1)

# Define basic data unit
DataUnit = namedtuple("DataUnit", APP_DATA_TYPES)
# fmt: on


# =========================================================
#              H E L P E R   F U N C T I O N S
# =========================================================
def debug_config_info(cliArgs, console=None):
    """Print/log some basic debug info.

    Args:
        cliArgs: CLI arguments used to start application
        console: Optional console object
    """

    if console:
        console.rule('Config Settings', style='grey', align='center')
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
    LOGGER.log_debug(
        f"Wi-Fi: {(f451Common.STATUS_YES if f451Common.check_wifi() else f451Common.STATUS_UNKNOWN)}"
    )

    # Display CLI args
    LOGGER.log_debug(f"CLI Args:\n{cliArgs}")


def prep_data_for_sensehat(inData, ledWidth):
    """Prep data for Sense HAT
    
    This function will filter data to ensure we don't have incorrect 
    outliers (e.g. from faulty sensors, etc.). The final data set will 
    have only valid values. Any invalid values will be replaced with 
    0's so that we can display the set on the Sense HAT LED.
    
    This will technically affect the min/max values for the set. However, 
    we're displaying this data on an 8x8 LED. So visual 'accuracy' is 
    already less than ideal ;-)

    NOTE: the data structure is more complex than we need for Sense HAT
    devices. But we want to maintain a basic level of compatibility with
    other f451 Labs modules.

    Args:
        inData: 'DataUnit' named tuple with 'raw' data from sensors
        ledWidth: width of LED display
        allowNone: 
    
    Returns:
        'DataUnit' named tuple with the following fields:
            data   = [list of values],
            valid  = <tuple with min/max>,
            unit   = <unit string>,
            label  = <label string>,
            limits = [list of limits]
    """
    # Data slice we can display on Sense HAT LED
    dataSlice = list(inData.data)[-ledWidth:]

    # Return filtered data
    dataClean = [i if f451Common.is_valid(i, inData.valid) else 0 for i in dataSlice]

    return f451SenseData.DataUnit(
        data = dataClean,
        valid = inData.valid,
        unit = inData.unit,
        label = inData.label,
        limits = inData.limits
    )


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
        '-V',
        '--version',
        action='store_true',
        help="display script version number and exit",
    )
    parser.add_argument(
        '-d', 
        '--debug', 
        action='store_true', 
        help="run script in debug mode"
    )
    parser.add_argument(
        '--noCLI',
        action='store_true',
        default=False,
        help='do not display output on CLI',
    )
    parser.add_argument(
        '--noLED',
        action='store_true',
        default=False,
        help='do not display output on LED',
    )
    parser.add_argument(
        '--progress',
        action='store_true',
        default=False,
        help="show upload progress bar on LED",
    )
    parser.add_argument(
        '--log',
        action='store',
        type=str,
        help="name of log file",
    )
    parser.add_argument(
        '--uploads',
        action='store',
        type=int,
        default=-1,
        help="number of uploads before exiting",
    )
    parser.add_argument(
        '-v',
        '--verbose',
        action='store_true',
        default=False,
        help="show output to CLI stdout",
    )

    return parser


async def send_data(*args):
    """Fake 'send' function"""
    time.sleep(5)

async def upload_demo_data(*args, **kwargs):
    """Fake upload function

    This helper function simulates parsing and uploading data 
    to some cloud service.

    Args:
        args:
            User can provide single 'dict' with data
        kwargs:
            User can provide individual data points as key-value pairs
    """
    # We combine 'args' and 'kwargs' to allow users to provide a 'dict' with
    # all data points and/or individual data points (which could override
    # values in the 'dict').
    upload = {**args[0], **kwargs} if args and isinstance(args[0], dict) else kwargs

    sendQ = []

    # Send download speed data ?
    if upload.get('data') is not None:
        sendQ.append(send_data()) # type: ignore

    # deviceID = SENSE_HAT.get_ID(DEF_ID_PREFIX)

    await asyncio.gather(*sendQ)


def get_random_demo_data(limits=None):
    """Generate random data

    Returns:
        'namedtuple' 'DataUnit with random demo data
    """
    return DataUnit(
        number1 = random.randint(1, 200),
        number2 = random.randint(0, 100)
    )


def btn_up(event):
    """SenseHat Joystick UP event

    Rotate display by -90 degrees and reset screen blanking
    """
    global app

    if event.action != f451SenseHat.BTN_RELEASE:
        SENSE_HAT.display_rotate(-1)
        app.displayUpdate = time.time()


def btn_down(event):
    """SenseHat Joystick DOWN event

    Rotate display by +90 degrees and reset screen blanking
    """
    global app

    if event.action != f451SenseHat.BTN_RELEASE:
        SENSE_HAT.display_rotate(1)
        app.displayUpdate = time.time()


def btn_left(event):
    """SenseHat Joystick LEFT event

    Switch display mode by 1 mode and reset screen blanking
    """
    global app

    if event.action != f451SenseHat.BTN_RELEASE:
        SENSE_HAT.update_display_mode(-1)
        app.displayUpdate = time.time()


def btn_right(event):
    """SenseHat Joystick RIGHT event

    Switch display mode by 1 mode and reset screen blanking
    """
    global app

    if event.action != f451SenseHat.BTN_RELEASE:
        SENSE_HAT.update_display_mode(1)
        app.displayUpdate = time.time()


def btn_middle(event):
    """SenseHat Joystick MIDDLE (down) event

    Turn display on/off
    """
    global app

    if event.action != f451SenseHat.BTN_RELEASE:
        # Wake up?
        if SENSE_HAT.displSleepMode:
            SENSE_HAT.update_sleep_mode(False)
            app.displayUpdate = time.time()
        else:
            SENSE_HAT.update_sleep_mode(True)



APP_JOYSTICK_ACTIONS = {
    f451SenseHat.KWD_BTN_UP: btn_up,
    f451SenseHat.KWD_BTN_DWN: btn_down,
    f451SenseHat.KWD_BTN_LFT: btn_left,
    f451SenseHat.KWD_BTN_RHT: btn_right,
    f451SenseHat.KWD_BTN_MDL: btn_middle,
}


def update_SenseHat_LED(sense, data):
    def _minMax(data):
        """Create min/max based on all collecxted data
        
        This will smooth out some hard edges that may occur
        when the data slice is to short.
        """
        scrubbed = [i for i in data if i is not None]
        return (min(scrubbed), max(scrubbed)) if scrubbed else (0, 0)

    # Check display mode. Each mode corresponds to a data type
    if sense.displMode == 1:
        dataClean = prep_data_for_sensehat(data.number1.as_tuple(), sense.widthLED)
        minMax = _minMax(data.number1.as_tuple().data)
        sense.display_as_graph(dataClean, minMax)

    elif sense.displMode == 2:
        dataClean = prep_data_for_sensehat(data.number2.as_tuple(), sense.widthLED)
        minMax = _minMax(data.number2.as_tuple().data)
        sense.display_as_graph(dataClean, minMax)

    else:  # Display sparkles
        sense.display_sparkle()


def init_app_runtime(config, cliArgs):
    runtime = f451Common.Runtime()

    runtime.ioFreq = config.get(const.KWD_FREQ, const.DEF_FREQ)
    runtime.ioDelay = config.get(const.KWD_DELAY, const.DEF_DELAY)
    runtime.ioWait = max(config.get(const.KWD_WAIT, const.DEF_WAIT), APP_MIN_SENSOR_READ_WAIT)
    runtime.ioThrottle = config.get(const.KWD_THROTTLE, const.DEF_THROTTLE)
    runtime.ioRounding = config.get(const.KWD_ROUNDING, const.DEF_ROUNDING)

    runtime.ioUploadAndExit = False

    if cliArgs.debug:
        runtime.logLvl = f451Logger.LOG_DEBUG
        runtime.debugMode = True
    else:
        runtime.logLvl = config.get(f451Logger.KWD_LOG_LEVEL, f451Logger.LOG_NOTSET)
        runtime.debugMode = (runtime.logLvl == f451Logger.LOG_DEBUG)

    runtime.timeSinceUpdate = float(0)
    runtime.timeUpdate = time.time()
    runtime.displayUpdate = runtime.timeUpdate
    runtime.uploadDelay = runtime.ioDelay
    runtime.maxUploads = int(cliArgs.uploads)
    runtime.numUploads = 0

    runtime.console = Console() # type: ignore

    return runtime


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
    global app

    # Parse CLI args and show 'help' and exit if no args
    cli = init_cli_parser()
    cliArgs, _ = cli.parse_known_args(cliArgs)
    if not cliArgs and len(sys.argv) == 1:
        cli.print_help(sys.stdout)
        sys.exit(0)

    if cliArgs.version:
        print(f'{APP_NAME} (v{APP_VERSION})')
        sys.exit(0)

    # Get core settings and initialize core data queue
    app = init_app_runtime(CONFIG, cliArgs)
    demoData = f451DemoData.DemoData(None, APP_MAX_DATA)

    # Update log file or level?
    if cliArgs.debug:
        LOGGER.set_log_level(f451Logger.LOG_DEBUG)

    if cliArgs.log is not None:
        LOGGER.set_log_file(app.logLvl, cliArgs.log)

    # Initialize Sense HAT joystick and LED display
    SENSE_HAT.joystick_init(**APP_JOYSTICK_ACTIONS)
    SENSE_HAT.display_init(**APP_DISPLAY_MODES)
    SENSE_HAT.update_sleep_mode(cliArgs.noLED)
    SENSE_HAT.displProgress = cliArgs.progress

    # -- Main application loop --
    exitNow = False
    app.console.rule(style='grey', align='center') # type: ignore
    SENSE_HAT.display_message(APP_NAME)
    print(f"{APP_NAME} (v{APP_VERSION})")
    print(f"Work start:  {(datetime.now()):%a %b %-d, %Y at %-I:%M:%S %p}")

    # If log level <= INFO
    LOGGER.log_info("-- START Data Logging --")

    while not exitNow:
        try:
            timeCurrent = time.time()
            app.timeSinceUpdate = timeCurrent - app.timeUpdate
            SENSE_HAT.update_sleep_mode(
                (timeCurrent - app.displayUpdate) > SENSE_HAT.displSleepTime, cliArgs.noLED, SENSE_HAT.displSleepMode
            )

            # --- Get magic data ---
            #
            # screen.update_action('Reading sensors …')
            newData = get_random_demo_data()
            #
            # ----------------------

            # Is it time to upload data?
            if app.timeSinceUpdate >= app.uploadDelay:
                try:
                    asyncio.run(
                        upload_demo_data(
                            data=newData.number1,
                            deviceID=f451Common.get_RPI_ID(f451Common.DEF_ID_PREFIX),
                        )
                    )

                except RequestError as e:
                    LOGGER.log_error(f"Application terminated: {e}")
                    sys.exit(1)

                except ThrottlingError:
                    # Keep increasing 'ioDelay' each time we get a 'ThrottlingError'
                    app.uploadDelay += app.ioThrottle

                else:
                    # Reset 'uploadDelay' back to normal 'ioFreq' on successful upload
                    app.numUploads += 1
                    app.uploadDelay = app.ioFreq
                    exitNow = exitNow or app.ioUploadAndExit
                    LOGGER.log_info(
                        f"Uploaded: Magic #: {round(newData.number1, app.ioRounding)}"
                    )

                finally:
                    app.timeUpdate = timeCurrent
                    exitNow = (app.maxUploads > 0) and (app.numUploads >= app.maxUploads)

            # Update data set and display to terminal as needed
            demoData.number1.data.append(newData.number1)
            demoData.number2.data.append(newData.number2)

            update_SenseHat_LED(SENSE_HAT, demoData)

            # Are we done? And do we have to wait a bit before next sensor read?
            if not exitNow:
                # If we'tre not done and there's a substantial wait before we can
                # read the sensors again (e.g. we only want to read sensors every
                # few minutes for whatever reason), then lets display and update
                # the progress bar as needed. Once the wait is done, we can go
                # through this whole loop all over again ... phew!
                # if app.ioWait > APP_MIN_PROG_WAIT:
                #     screen.update_progress(None, 'Waiting for sensors …')
                #     for i in range(app.ioWait):
                #         screen.update_progress(int(i / app.ioWait * 100))
                #         time.sleep(APP_WAIT_1SEC)
                #     screen.update_action()
                # else:
                #     screen.update_action()
                #     time.sleep(app.ioWait)
                time.sleep(app.ioWait)

                # Update Sense HAT prog bar as needed
                SENSE_HAT.display_progress(app.timeSinceUpdate / app.uploadDelay)

        except KeyboardInterrupt:
            exitNow = True

    # If log level <= INFO
    LOGGER.log_info("-- END Data Logging --")

    # A bit of clean-up before we exit ...
    SENSE_HAT.display_reset()
    SENSE_HAT.display_off()

    # ... and display summary info
    print(f"Work end:    {(datetime.now()):%a %b %-d, %Y at %-I:%M:%S %p}")
    print(f"Num uploads: {app.numUploads}")
    app.console.rule(style='grey', align='center') # type: ignore
    pprint(locals(), expand_all=True)
    pprint(CONFIG, expand_all=True)

    if app.debugMode:
        debug_config_info(cliArgs, app.console)


# =========================================================
#            G L O B A L   C A T C H - A L L
# =========================================================
if __name__ == '__main__':
    main()  # pragma: no cover
