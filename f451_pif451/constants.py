"""Global constants for f451 Labs piF451 application.

This module holds all global constants used within the components of 
the f451 Labs piF451 application. Some of the constants are used as 
keyword equivalents for attributes listed in the `settings.toml` file.
"""

# =========================================================
#              M I S C .   C O N S T A N T S
# =========================================================
DEF_FREQ = 600              # Default delay between uploads in seconds
DEF_DELAY = 300             # Default delay before first upload in seconds
DEF_WAIT = 1                # Default delay between sensor reads
DEF_THROTTLE = 120          # Default additional delay on 'ThrottlingError'
DEF_ROUNDING = 2            # Default 'rounding' precision for uploaded data

# =========================================================
#    K E Y W O R D S   F O R   C O N F I G   F I L E S
# =========================================================
KWD_FREQ = "FREQ"
KWD_DELAY = "DELAY"
KWD_WAIT = "WAIT"
KWD_THROTTLE = "THROTTLE"
KWD_ROUNDING = "ROUNDING"

KWD_FEED_DWNLD = "FEED_DWNLD"
KWD_FEED_UPLD = "FEED_UPLD"
KWD_FEED_PING = "FEED_PING"

KWD_DATA_DWNLD = "download"
KWD_DATA_UPLD = "upload"
KWD_DATA_PING = "ping"

# -- Additional support for environment data --
KWD_FEED_TEMPS = "FEED_TEMPS"
KWD_FEED_PRESS = "FEED_PRESS"
KWD_FEED_HUMID = "FEED_HUMID"

KWD_DATA_TEMPS = "temperature"
KWD_DATA_PRESS = "pressure"
KWD_DATA_HUMID = "humidity"


# =========================================================
#   C O N S T A N T S   F O R   D I S P L A Y   M O D E S
# =========================================================
DISPL_SPARKLE = 0               # Display sparkles
DISPL_DWNLD = DISPL_TEMP  = 1   # Display download or temp
DISPL_UPLD  = DISPL_PRESS = 2   # Display upload or pressure
DISPL_PING  = DISPL_HUMID = 3   # Display ping or humidity

MIN_DISPL = DISPL_SPARKLE       # Cannot be smaller than smallest DISPL_xx value     
MAX_DISPL = 3                   # Cannot be larger than largest DISPL_xx value 

# -- SpeedTest --
MBITS_PER_SEC = 1048576         # 1 MB = 1024 * 1024
MAX_SPEED_MB = 3000             # Max Download speed = 3 Gbps = 3,000 Mbps