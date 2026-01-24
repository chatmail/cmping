
# cmping changelog 

## 0.13.0

### Features

- Add IP address support with automated account setup and progress tracking
  - Accept IPv4 and IPv6 addresses as relay endpoints
  - Generate dclogin URLs with random credentials for IP-based accounts
  - Display N/M progress spinner during account setup
  - Provide clear error feedback when accounts fail to configure

- Add `-g NUMRECIPIENTS` option for multi-recipient group chat testing
  - Creates a single group chat with N recipients instead of N separate 1:1 chats
  - Shows animated N/M progress ratio that updates in-place
  - Displays MIN/MAX timing: first receiver time and elapsed time to last receiver
  - All receivers explicitly accept group before pinging starts
  - Properly handles group member addition using Contact objects
  - Message verification with 30-second timeout per receiver
## 0.11.2

- catch keyboardinterrupt and exit with code 2

## 0.11.1

- allow higher rpc-client/server versions

## 0.11.0

- show clock-measurements with "-v" 

## 0.10.0

- added -v option for more verbosity (showing INFO messages from core) 

- simplified internal argument passing 

- notice and print failure string when a message failed to deliver 

- return error code 1 if there was message loss
