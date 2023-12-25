# Instructions for f451-piF451 SysMon v1.0.3

## Custom application settings in SETTINGS.TOML

The 'settings.toml' file holds various custom application settings and secrets (e.g. Adafruit IO keys, etc.) and this file should **NOT** be included in 'git' commits.

It is recommended to copy the '*settings.example*' to '*settings.toml*' and then customize the values in '*settings.toml*' as nedeed for the specific device that the application is running on.

### Adafruit IO settings

- **AIO_USERNAME**: 'string' - Adafruit IO username
- **AIO_KEY**: 'string' - Adafruit IO key
- **AIO_UPLOAD**: 'string' - yes | force | no
  - "yes" - *upload if feed available*
  - "force" - *exit if feed invalid*
  - "no" - *do not upload data*

- **FEED_DWNLD**: 'string' - Adafruit IO feed key for 'download' feed
- **FEED_UPLD**: 'string' - Adafruit IO feed key for 'upload' feed
- **FEED_PING**: 'string' - Adafruit IO feed key for 'ping' feed

### Misc. Application Defaults

- **ROTATION**: 'int' - 0 | 90 | 180 | 270 degrees to turn 8x8 LED display
  - 90 | 270 - *top of LED will point toward/away RPI HDMI*
  - 0 | 180 - *top of LED will point away/toward RPI USB*

- **DISPLAY**: 'int' - 0..3
  - 1..3 - *display modes with single data point (e.g. download speed, etc.) and scrolling bar graph*
  - 0 - *display is 'sparkle' mode where data is collected and uploaded but not displayed*

- **DELAY**: 'int' - delay in seconds between uploads to Adafruit IO.
  - Smaller number means more freq uploads and higher data rate
- **WAIT**: 'int' - delay in seconds between sensor reads
- **THROTTLE**: 'int' - additional delay in seconds to be applied on Adafruit IO 'ThottlingError'

- **PROGRESS**: 'string' - on | off
  - "on" - *show 'wait for upload' progress bar on LED*
  - "off" - *do not show progress bar*

- **SLEEP**: 'int' - delay in seconds until LED is blanked for "screen saver" mode

- **LOGLVL**: 'string' - debug | info | error
  - *Logging levels (see: [Python docs](https://docs.python.org/3/library/logging.html#logging-levels) for more info)*

- **LOGFILE**: 'string' - path and file name for log file

## Dependencies

The following special libraries are required:

- [adafruit-io](https://pypi.org/project/adafruit-io/) - only install if you have physical Sense HAT
- [speedtest-cli](https://pypi.org/project/speedtest-cli/) - only used for internet speed tests 

## How to use

The **f451 Labs SysMon** application can be launched as follows:

```bash
$ python -m f451_pif451.sysmon [<options>]

# If you have installed the 'f451 Labs piF451' module 
# using the 'pip install'
$ sysmon [<options>]

# Use CLI arg '-h' to see available options
$ sysmon -h 
```

You can adjust the settings in the `settings.toml` file. For example, if you change the `PROGRESS` setting to 1, then the Sense HAT LED will display a progress bar indicvating when the next (simulated) upload will happen.

Also, the joystick on the Sense HAT allows you to rotate the LED screen and switch between display modes. There is also a 'sleep mode' which turns off the display automatically after a certain amount of time. You can also turn on/off the LED display by pushing the joystick down.

```toml
# File: settings.toml
...
PROGRESS = 1    # [0|1] - 1 = show upload progress bar on LCD
SLEEP = 600     # Delay in seconds until screen is blanked
...
```

Finally you can exit the application using the `ctrl-c` command. If you use the `--uploads N` commandline argument, then the application will stop after *N* (simulated) uploads.

```bash
# Stop after 10 uploads
$ sysmon --uploads 10

# Show 'progress bar' regardless of setting in 'toml' file
$ sysmon --progress
```

## How to test

**TO DO -- write tests for SysMon -- this feature has not yet been implemented!**
The tests are written for [pytest](https://docs.pytest.org/en/7.1.x/contents.html) and we use markers to separate out tests that require the actual Sense HAT hardware. Some tests do not rely on the hardware to be prexent. However, those tests rely on the `pytest-mock` module to be present.

```bash
# Run all tests (except marked 'skip')
$ pytest

# Run tests with 'hardware' marker
$ pytest -m "hardware"

# Run tests without 'hardware' marker
$ pytest -m "not hardware"
```
