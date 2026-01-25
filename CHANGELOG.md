
# cmping changelog 

## 0.15.0

### Improvements

- Simplified progress display with cleaner UI and animated spinners
  - Profile setup shows animated spinner with N/M counter: "# Setting up profiles ⠋ N/M"
  - Profile online waiting shows animated spinner: "# Waiting for profiles to be online ⠋"
  - Combined "promoting group chat" and "waiting for receivers" into single line: "# Waiting for receivers to come online N/M"
  - CMPING line now shows only the number of receivers instead of listing all addresses: "group with N receivers"
  - In verbose mode (`-v`), all receiver addresses are printed after they come online
  - Changed terminology from "account" to "profile" in user-facing messages (API calls still use "account")

## 0.14.0

### Features

- Add comprehensive error event logging with `-v` flag
  - Error events during account setup and configuration are now logged
  - Error events during group joining phase are displayed
  - Error and failed message events during ping operations are shown
  - All error messages use consistent ✗ prefix for easy identification

- Concurrent receiver joining with live progress indicator
  - Changed from sequential to parallel receiver joining using threads
  - Shows real-time N/M progress spinner (e.g., "# waiting for receivers to join group 2/5")
  - Significantly faster group setup with multiple receivers
  - Improved timeout and error handling per receiver

### Improvements

- Refactored code structure for better maintainability
  - Extracted `setup_accounts()` function for account creation
  - Extracted `create_and_promote_group()` function for group management
  - Extracted `wait_for_receivers_to_join()` function for concurrent joining
  - Main `perform_ping()` function is now cleaner and easier to follow

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
