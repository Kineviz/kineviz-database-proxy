#!/bin/bash
CURRENT_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="$(dirname "$CURRENT_SCRIPT_DIR")/docker/data/"

SYSTEM="$(uname -o)"
if [ $SYSTEM == "Msys" ]
then
    export MSYS2_ARG_CONV_EXCL="*"
    DATA_DIR="$(cygpath -w $DATA_DIR)"
    echo "Msys"
else
    echo "GNU/Linux"
fi

docker rm -f kineviz-database-proxy 2>/dev/null

docker run -d -p 9080:9080 \
--name kineviz-database-proxy \
-v  ${DATA_DIR}:/app/config \
kineviz/kineviz-database-proxy:latest

