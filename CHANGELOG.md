
# cmping changelog 

## Unreleased

### Features

- Add `-g NUMRECIPIENTS` option for multi-recipient group chat testing
  - Creates a single group chat with N recipients instead of N separate 1:1 chats
  - Shows animated N/M progress ratio that updates in-place
  - Displays MIN/MAX timing: first receiver time and elapsed time to last receiver
  - All receivers explicitly accept group before pinging starts
  - Properly handles group member addition using Contact objects
  - Message verification with 30-second timeout per receiver

## 0.11.0

- show clock-measurements with "-v" 

## 0.10.0

- added -v option for more verbosity (showing INFO messages from core) 

- simplified internal argument passing 

- notice and print failure string when a message failed to deliver 

- return error code 1 if there was message loss
