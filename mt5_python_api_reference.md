# MetaTrader 5 Python API Reference

> **Complete reference for the `MetaTrader5` Python package**
> Source: [MQL5 Official Documentation](https://www.mql5.com/en/docs/python_metatrader5)
> Import: `import MetaTrader5 as mt5`

---

## Table of Contents

### Connection & Info
1. [initialize](#1-initialize) - Establish connection with MT5 terminal
2. [login](#2-login) - Connect to a trading account
3. [shutdown](#3-shutdown) - Close connection to MT5 terminal
4. [version](#4-version) - Get MT5 terminal version
5. [last_error](#5-last_error) - Get last error info
6. [account_info](#6-account_info) - Get current account info
7. [terminal_info](#7-terminal_info) - Get terminal status and settings

### Symbols
8. [symbols_total](#8-symbols_total) - Get number of all symbols
9. [symbols_get](#9-symbols_get) - Get all symbols with optional filter
10. [symbol_info](#10-symbol_info) - Get data on a specific symbol
11. [symbol_info_tick](#11-symbol_info_tick) - Get last tick for a symbol
12. [symbol_select](#12-symbol_select) - Select/deselect symbol in MarketWatch

### Market Depth
13. [market_book_add](#13-market_book_add) - Subscribe to Market Depth events
14. [market_book_get](#14-market_book_get) - Get Market Depth data
15. [market_book_release](#15-market_book_release) - Unsubscribe from Market Depth events

### Historical Data
16. [copy_rates_from](#16-copy_rates_from) - Get bars from a date
17. [copy_rates_from_pos](#17-copy_rates_from_pos) - Get bars from an index
18. [copy_rates_range](#18-copy_rates_range) - Get bars in date range
19. [copy_ticks_from](#19-copy_ticks_from) - Get ticks from a date
20. [copy_ticks_range](#20-copy_ticks_range) - Get ticks in date range

### Active Orders
21. [orders_total](#21-orders_total) - Get number of active orders
22. [orders_get](#22-orders_get) - Get active orders

### Order Operations
23. [order_calc_margin](#23-order_calc_margin) - Calculate margin for an order
24. [order_calc_profit](#24-order_calc_profit) - Calculate profit for an order
25. [order_check](#25-order_check) - Check funds sufficiency for an order
26. [order_send](#26-order_send) - Send a trading request

### Positions
27. [positions_total](#27-positions_total) - Get number of open positions
28. [positions_get](#28-positions_get) - Get open positions

### Trade History
29. [history_orders_total](#29-history_orders_total) - Get number of history orders
30. [history_orders_get](#30-history_orders_get) - Get history orders
31. [history_deals_total](#31-history_deals_total) - Get number of history deals
32. [history_deals_get](#32-history_deals_get) - Get history deals

### Enumerations Reference
- [TIMEFRAME](#timeframe-enumeration)
- [ORDER_TYPE](#order_type-enumeration)
- [COPY_TICKS](#copy_ticks-enumeration)
- [TICK_FLAG](#tick_flag-enumeration)
- [TRADE_REQUEST_ACTIONS](#trade_request_actions-enumeration)
- [ORDER_TYPE_FILLING](#order_type_filling-enumeration)
- [ORDER_TYPE_TIME](#order_type_time-enumeration)
- [TradeRequest Structure](#traderequest-structure)
- [Error Codes](#error-codes)

---

## 1. initialize

Establish a connection with the MetaTrader 5 terminal.

### Signatures

```python
# Call without parameters (auto-find terminal)
initialize()

# Call with path to terminal
initialize(
    path                # path to the MetaTrader 5 terminal EXE file
)

# Call with full trading account parameters
initialize(
    path,               # path to the MetaTrader 5 terminal EXE file
    login=LOGIN,        # account number
    password="PASSWORD",# password
    server="SERVER",    # server name as specified in the terminal
    timeout=TIMEOUT,    # timeout in milliseconds
    portable=False      # portable mode
)
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | str | Optional | Path to metatrader.exe or metatrader64.exe. Indicated first without a parameter name. If not specified, the module attempts to find the executable on its own. |
| `login` | int | Optional | Trading account number. If not specified, the last trading account is used. |
| `password` | str | Optional | Trading account password. If not set, the password saved in the terminal database is applied automatically. |
| `server` | str | Optional | Trade server name. If not set, the server saved in the terminal database is applied automatically. |
| `timeout` | int | Optional | Connection timeout in milliseconds. Default: 60000 (60 seconds). |
| `portable` | bool | Optional | Flag for terminal launch in portable mode. Default: False. |

### Return Value

`True` if successful connection to the MetaTrader 5 terminal, otherwise `False`.

### Note

If required, the MetaTrader 5 terminal is launched to establish connection when executing the `initialize()` call.

### Example

```python
import MetaTrader5 as mt5

# display data on the MetaTrader 5 package
print("MetaTrader5 package author: ", mt5.__author__)
print("MetaTrader5 package version: ", mt5.__version__)

# establish MetaTrader 5 connection to a specified trading account
if not mt5.initialize(login=25115284, server="MetaQuotes-Demo", password="4zatlbqx"):
    print("initialize() failed, error code =", mt5.last_error())
    quit()

# display data on connection status, server name and trading account
print(mt5.terminal_info())
# display data on MetaTrader 5 version
print(mt5.version())

# shut down connection to the MetaTrader 5 terminal
mt5.shutdown()
```

---

## 2. login

Connect to a trading account using specified parameters.

### Signature

```python
login(
    login,              # account number
    password="PASSWORD",# password
    server="SERVER",    # server name as specified in the terminal
    timeout=TIMEOUT     # timeout
)
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `login` | int | **Required** | Trading account number. Required unnamed parameter. |
| `password` | str | Optional | Trading account password. If not set, the password saved in the terminal database is applied automatically. |
| `server` | str | Optional | Trade server name. If not set, the last used server is applied automatically. |
| `timeout` | int | Optional | Connection timeout in milliseconds. Default: 60000 (60 seconds). If connection is not established within the specified time, the call is forcibly terminated. |

### Return Value

`True` if successful connection to the trade account, otherwise `False`.

### Example

```python
import MetaTrader5 as mt5

# establish connection to the MetaTrader 5 terminal
if not mt5.initialize():
    print("initialize() failed, error code =", mt5.last_error())
    quit()

# connect to the trade account without specifying a password and a server
account = 17221085
authorized = mt5.login(account)  # terminal database password applied if connection data is set to be remembered

if authorized:
    print("connected to account #{}".format(account))
else:
    print("failed to connect at account #{}, error code: {}".format(account, mt5.last_error()))

# now connect to another trading account specifying the password
account = 25115284
authorized = mt5.login(account, password="gqrtz0lbdm")

if authorized:
    # display trading account data 'as is'
    print(mt5.account_info())
    # display trading account data in the form of a dictionary
    print("Show account_info()._asdict():")
    account_info_dict = mt5.account_info()._asdict()
    for prop in account_info_dict:
        print("  {}={}".format(prop, account_info_dict[prop]))
else:
    print("failed to connect at account #{}, error code: {}".format(account, mt5.last_error()))

# shut down connection to the MetaTrader 5 terminal
mt5.shutdown()
```

---

## 3. shutdown

Close the previously established connection to the MetaTrader 5 terminal.

### Signature

```python
shutdown()
```

### Parameters

None.

### Return Value

`None`.

### Example

```python
import MetaTrader5 as mt5

# establish connection to the MetaTrader 5 terminal
if not mt5.initialize():
    print("initialize() failed")
    quit()

# display data on connection status, server name and trading account
print(mt5.terminal_info())
# display data on MetaTrader 5 version
print(mt5.version())

# shut down connection to the MetaTrader 5 terminal
mt5.shutdown()
```

---

## 4. version

Return the MetaTrader 5 terminal version.

### Signature

```python
version()
```

### Parameters

None.

### Return Value

Returns the terminal version, build and release date as a tuple of three values. Returns `None` in case of an error. Use `last_error()` to get error info.

| Type | Description | Sample Value |
|------|-------------|-------------|
| integer | MetaTrader 5 terminal version | 500 |
| integer | Build | 2007 |
| string | Build release date | '25 Feb 2019' |

### Example

```python
import MetaTrader5 as mt5
import pandas as pd

# establish connection to the MetaTrader 5 terminal
if not mt5.initialize():
    print("initialize() failed, error code =", mt5.last_error())
    quit()

# display data on MetaTrader 5 version
print(mt5.version())
# display data on connection status, server name and trading account 'as is'
print(mt5.terminal_info())

# get properties in the form of a dictionary
terminal_info_dict = mt5.terminal_info()._asdict()
# convert the dictionary into DataFrame and print
df = pd.DataFrame(list(terminal_info_dict.items()), columns=['property', 'value'])
print("terminal_info() as dataframe:")
print(df[:-1])

# shut down connection to the MetaTrader 5 terminal
mt5.shutdown()
```

---

## 5. last_error

Return data on the last error.

### Signature

```python
last_error()
```

### Parameters

None.

### Return Value

Returns the last error code and description as a tuple.

### Error Codes

| Constant | Value | Description |
|----------|-------|-------------|
| RES_S_OK | 1 | generic success |
| RES_E_FAIL | -1 | generic fail |
| RES_E_INVALID_PARAMS | -2 | invalid arguments/parameters |
| RES_E_NO_MEMORY | -3 | no memory condition |
| RES_E_NOT_FOUND | -4 | no history |
| RES_E_INVALID_VERSION | -5 | invalid version |
| RES_E_AUTH_FAILED | -6 | authorization failed |
| RES_E_UNSUPPORTED | -7 | unsupported method |
| RES_E_AUTO_TRADING_DISABLED | -8 | auto-trading disabled |
| RES_E_INTERNAL_FAIL | -10000 | internal IPC general error |
| RES_E_INTERNAL_FAIL_SEND | -10001 | internal IPC send failed |
| RES_E_INTERNAL_FAIL_RECEIVE | -10002 | internal IPC recv failed |
| RES_E_INTERNAL_FAIL_INIT | -10003 | internal IPC initialization fail |
| RES_E_INTERNAL_FAIL_CONNECT | -10003 | internal IPC no ipc |
| RES_E_INTERNAL_FAIL_TIMEOUT | -10005 | internal timeout |

### Example

```python
import MetaTrader5 as mt5

# establish connection to the MetaTrader 5 terminal
if not mt5.initialize():
    print("initialize() failed, error code =", mt5.last_error())
    quit()

# shut down connection to the MetaTrader 5 terminal
mt5.shutdown()
```

---

## 6. account_info

Get info on the current trading account.

### Signature

```python
account_info()
```

### Parameters

None.

### Return Value

Returns info as a named tuple structure (namedtuple). Returns `None` in case of an error. Use `last_error()` to get error info.

### Note

The function returns all data that can be obtained using `AccountInfoInteger`, `AccountInfoDouble` and `AccountInfoString` in one call.

**Returned fields include:** `login`, `trade_mode`, `leverage`, `limit_orders`, `margin_so_mode`, `trade_allowed`, `trade_expert`, `margin_mode`, `currency_digits`, `fifo_close`, `balance`, `credit`, `profit`, `equity`, `margin`, `margin_free`, `margin_level`, `margin_so_call`, `margin_so_so`, `margin_initial`, `margin_maintenance`, `assets`, `liabilities`, `commission_blocked`, `name`, `server`, `currency`, `company`.

### Example

```python
import MetaTrader5 as mt5
import pandas as pd

# establish connection to the MetaTrader 5 terminal
if not mt5.initialize():
    print("initialize() failed, error code =", mt5.last_error())
    quit()

# connect to the trade account specifying a password and a server
authorized = mt5.login(25115284, password="gqz0343lbdm")

if authorized:
    account_info = mt5.account_info()
    if account_info != None:
        # display trading account data 'as is'
        print(account_info)
        # display trading account data in the form of a dictionary
        print("Show account_info()._asdict():")
        account_info_dict = mt5.account_info()._asdict()
        for prop in account_info_dict:
            print("  {}={}".format(prop, account_info_dict[prop]))
        print()

        # convert the dictionary into DataFrame and print
        df = pd.DataFrame(list(account_info_dict.items()), columns=['property', 'value'])
        print("account_info() as dataframe:")
        print(df)

# shut down connection to the MetaTrader 5 terminal
mt5.shutdown()
```

---

## 7. terminal_info

Get the connected MetaTrader 5 client terminal status and settings.

### Signature

```python
terminal_info()
```

### Parameters

None.

### Return Value

Returns info as a named tuple structure (namedtuple). Returns `None` in case of an error. Use `last_error()` to get error info.

### Note

The function returns all data that can be obtained using `TerminalInfoInteger`, `TerminalInfoDouble` and `TerminalInfoString` in one call.

**Returned fields include:** `community_account`, `community_connection`, `connected`, `dlls_allowed`, `trade_allowed`, `tradeapi_disabled`, `email_enabled`, `ftp_enabled`, `notifications_enabled`, `mqid`, `build`, `maxbars`, `codepage`, `ping_last`, `community_balance`, `retransmission`, `company`, `name`, `language`, `path`, `data_path`, `commondata_path`.

### Example

```python
import MetaTrader5 as mt5
import pandas as pd

# establish connection to the MetaTrader 5 terminal
if not mt5.initialize():
    print("initialize() failed, error code =", mt5.last_error())
    quit()

# display data on MetaTrader 5 version
print(mt5.version())

# display info on the terminal settings and status
terminal_info = mt5.terminal_info()
if terminal_info != None:
    # display the terminal data 'as is'
    print(terminal_info)
    # display data in the form of a dictionary
    print("Show terminal_info()._asdict():")
    terminal_info_dict = mt5.terminal_info()._asdict()
    for prop in terminal_info_dict:
        print("  {}={}".format(prop, terminal_info_dict[prop]))
    print()

    # convert the dictionary into DataFrame and print
    df = pd.DataFrame(list(terminal_info_dict.items()), columns=['property', 'value'])
    print("terminal_info() as dataframe:")
    print(df)

# shut down connection to the MetaTrader 5 terminal
mt5.shutdown()
```

---

## 8. symbols_total

Get the number of all financial instruments in the MetaTrader 5 terminal.

### Signature

```python
symbols_total()
```

### Parameters

None.

### Return Value

Integer value.

### Note

The function is similar to `SymbolsTotal()`. However, it returns the number of all symbols including custom ones and the ones disabled in MarketWatch.

### Example

```python
import MetaTrader5 as mt5

# establish connection to the MetaTrader 5 terminal
if not mt5.initialize():
    print("initialize() failed, error code =", mt5.last_error())
    quit()

# get the number of financial instruments
symbols = mt5.symbols_total()
if symbols > 0:
    print("Total symbols =", symbols)
else:
    print("symbols not found")

# shut down connection to the MetaTrader 5 terminal
mt5.shutdown()
```

---

## 9. symbols_get

Get all financial instruments from the MetaTrader 5 terminal.

### Signature

```python
symbols_get(
    group="GROUP"      # symbol selection filter
)
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `group` | str | Optional | The filter for arranging a group of necessary symbols. If specified, only symbols meeting the criteria are returned. |

### Return Value

Returns symbols as a tuple. Returns `None` in case of an error. Use `last_error()` to get error info.

### Note

- The `group` parameter allows sorting out symbols by name. `*` can be used at the beginning and end of a string.
- The `group` parameter may contain several comma-separated conditions. A condition can be set as a mask using `*`. The logical negation symbol `!` can be used for exclusion.
- All conditions are applied sequentially: include conditions first, then exclusion conditions.
- Example: `group="*, !EUR"` selects all symbols first, then excludes those containing "EUR".
- Unlike `symbol_info()`, `symbols_get()` returns data on all requested symbols within a single call.

### Example

```python
import MetaTrader5 as mt5

# establish connection to the MetaTrader 5 terminal
if not mt5.initialize():
    print("initialize() failed, error code =", mt5.last_error())
    quit()

# get all symbols
symbols = mt5.symbols_get()
print('Symbols:', len(symbols))
count = 0
# display the first five ones
for s in symbols:
    count += 1
    print("{}. {}".format(count, s.name))
    if count == 5: break
print()

# get symbols containing RU in their names
ru_symbols = mt5.symbols_get("*RU*")
print('len(*RU*):', len(ru_symbols))
for s in ru_symbols:
    print(s.name)
print()

# get symbols whose names do not contain USD, EUR, JPY and GBP
group_symbols = mt5.symbols_get(group="*,!*USD*,!*EUR*,!*JPY*,!*GBP*")
print('len(*,!*USD*,!*EUR*,!*JPY*,!*GBP*):', len(group_symbols))
for s in group_symbols:
    print(s.name, ":", s)

# shut down connection to the MetaTrader 5 terminal
mt5.shutdown()
```

---

## 10. symbol_info

Get data on the specified financial instrument.

### Signature

```python
symbol_info(
    symbol      # financial instrument name
)
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `symbol` | str | **Required** | Financial instrument name. Required unnamed parameter. |

### Return Value

Returns info as a named tuple structure (namedtuple). Returns `None` in case of an error. Use `last_error()` to get error info.

### Note

The function returns all data that can be obtained using `SymbolInfoInteger`, `SymbolInfoDouble` and `SymbolInfoString` in one call.

**Key returned fields include:** `custom`, `chart_mode`, `select`, `visible`, `digits`, `spread`, `spread_float`, `ticks_bookdepth`, `trade_calc_mode`, `trade_mode`, `trade_stops_level`, `trade_freeze_level`, `trade_exemode`, `swap_mode`, `swap_rollover3days`, `bid`, `bidhigh`, `bidlow`, `ask`, `askhigh`, `asklow`, `last`, `point`, `trade_tick_value`, `trade_tick_size`, `trade_contract_size`, `volume_min`, `volume_max`, `volume_step`, `swap_long`, `swap_short`, `currency_base`, `currency_profit`, `currency_margin`, `description`, `name`, `path`, and many more.

### Example

```python
import MetaTrader5 as mt5

# establish connection to the MetaTrader 5 terminal
if not mt5.initialize():
    print("initialize() failed, error code =", mt5.last_error())
    quit()

# attempt to enable the display of the EURJPY symbol in MarketWatch
selected = mt5.symbol_select("EURJPY", True)
if not selected:
    print("Failed to select EURJPY")
    mt5.shutdown()
    quit()

# display EURJPY symbol properties
symbol_info = mt5.symbol_info("EURJPY")
if symbol_info != None:
    # display the terminal data 'as is'
    print(symbol_info)
    print("EURJPY: spread =", symbol_info.spread, "  digits =", symbol_info.digits)
    # display symbol properties as a dictionary
    print("Show symbol_info(\"EURJPY\")._asdict():")
    symbol_info_dict = mt5.symbol_info("EURJPY")._asdict()
    for prop in symbol_info_dict:
        print("  {}={}".format(prop, symbol_info_dict[prop]))

# shut down connection to the MetaTrader 5 terminal
mt5.shutdown()
```

---

## 11. symbol_info_tick

Get the last tick for the specified financial instrument.

### Signature

```python
symbol_info_tick(
    symbol      # financial instrument name
)
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `symbol` | str | **Required** | Financial instrument name. Required unnamed parameter. |

### Return Value

Returns info as a tuple with fields: `time`, `bid`, `ask`, `last`, `volume`, `time_msc`, `flags`, `volume_real`. Returns `None` in case of an error. Use `last_error()` to get error info.

### Note

The function is similar to `SymbolInfoTick`.

### Example

```python
import MetaTrader5 as mt5

# establish connection to the MetaTrader 5 terminal
if not mt5.initialize():
    print("initialize() failed, error code =", mt5.last_error())
    quit()

# attempt to enable the display of the GBPUSD in MarketWatch
selected = mt5.symbol_select("GBPUSD", True)
if not selected:
    print("Failed to select GBPUSD")
    mt5.shutdown()
    quit()

# display the last GBPUSD tick
lasttick = mt5.symbol_info_tick("GBPUSD")
print(lasttick)

# display tick field values in the form of a dictionary
print("Show symbol_info_tick(\"GBPUSD\")._asdict():")
symbol_info_tick_dict = mt5.symbol_info_tick("GBPUSD")._asdict()
for prop in symbol_info_tick_dict:
    print("  {}={}".format(prop, symbol_info_tick_dict[prop]))

# shut down connection to the MetaTrader 5 terminal
mt5.shutdown()
```

---

## 12. symbol_select

Select a symbol in the MarketWatch window or remove a symbol from the window.

### Signature

```python
symbol_select(
    symbol,         # financial instrument name
    enable=None     # enable or disable
)
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `symbol` | str | **Required** | Financial instrument name. Required unnamed parameter. |
| `enable` | bool | Optional | Switch. If `False`, the symbol is removed from MarketWatch. Otherwise, it is selected in MarketWatch. A symbol cannot be removed if open charts or positions exist for it. |

### Return Value

`True` if successful, otherwise `False`.

### Note

The function is similar to `SymbolSelect`.

### Example

```python
import MetaTrader5 as mt5
import pandas as pd

# establish connection to the MetaTrader 5 terminal
if not mt5.initialize(login=25115284, server="MetaQuotes-Demo", password="4zatlbqx"):
    print("initialize() failed, error code =", mt5.last_error())
    quit()

# attempt to enable the display of EURCAD in MarketWatch
selected = mt5.symbol_select("EURCAD", True)
if not selected:
    print("Failed to select EURCAD, error code =", mt5.last_error())
else:
    symbol_info = mt5.symbol_info("EURCAD")
    print(symbol_info)
    print("EURCAD: currency_base =", symbol_info.currency_base,
          "  currency_profit =", symbol_info.currency_profit,
          "  currency_margin =", symbol_info.currency_margin)

# shut down connection to the MetaTrader 5 terminal
mt5.shutdown()
```

---

## 13. market_book_add

Subscribes the MetaTrader 5 terminal to the Market Depth change events for a specified symbol.

### Signature

```python
market_book_add(
    symbol      # financial instrument name
)
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `symbol` | str | **Required** | Financial instrument name. Required unnamed parameter. |

### Return Value

`True` if successful, otherwise `False`.

### Note

The function is similar to `MarketBookAdd`.

---

## 14. market_book_get

Returns a tuple from BookInfo featuring Market Depth entries for the specified symbol.

### Signature

```python
market_book_get(
    symbol      # financial instrument name
)
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `symbol` | str | **Required** | Financial instrument name. Required unnamed parameter. |

### Return Value

Returns the Market Depth content as a tuple of `BookInfo` entries featuring order type, price and volume in lots. `BookInfo` is similar to the `MqlBookInfo` structure. Returns `None` in case of an error. Use `last_error()` to get error info.

### Note

- The subscription to Market Depth change events must be performed first using `market_book_add()`.
- The function is similar to `MarketBookGet`.

### Example

```python
import MetaTrader5 as mt5
import time

# establish connection to the MetaTrader 5 terminal
if not mt5.initialize():
    print("initialize() failed, error code =", mt5.last_error())
    mt5.shutdown()
    quit()

# subscribe to market depth updates for EURUSD (Depth of Market)
if mt5.market_book_add('EURUSD'):
    # get the market depth data 10 times in a loop
    for i in range(10):
        # get the market depth content (Depth of Market)
        items = mt5.market_book_get('EURUSD')
        # display the entire market depth 'as is' in a single string
        print(items)
        # now display each order separately for more clarity
        if items:
            for it in items:
                # order content
                print(it._asdict())
        # pause for 5 seconds before the next request
        time.sleep(5)
    # cancel the subscription
    mt5.market_book_release('EURUSD')
else:
    print("mt5.market_book_add('EURUSD') failed, error code =", mt5.last_error())

# shut down connection to the MetaTrader 5 terminal
mt5.shutdown()
```

---

## 15. market_book_release

Cancels subscription of the MetaTrader 5 terminal to the Market Depth change events for a specified symbol.

### Signature

```python
market_book_release(
    symbol      # financial instrument name
)
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `symbol` | str | **Required** | Financial instrument name. Required unnamed parameter. |

### Return Value

`True` if successful, otherwise `False`.

### Note

The function is similar to `MarketBookRelease`.

---

## 16. copy_rates_from

Get bars from the MetaTrader 5 terminal starting from the specified date.

### Signature

```python
copy_rates_from(
    symbol,         # symbol name
    timeframe,      # timeframe
    date_from,      # initial bar open date
    count           # number of bars
)
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `symbol` | str | **Required** | Financial instrument name, e.g. "EURUSD". |
| `timeframe` | int | **Required** | Timeframe from the [TIMEFRAME](#timeframe-enumeration) enumeration. |
| `date_from` | datetime/int | **Required** | Date of the first bar. Set by `datetime` object or seconds since 1970.01.01. |
| `count` | int | **Required** | Number of bars to receive. |

### Return Value

Returns bars as a numpy array with columns: `time`, `open`, `high`, `low`, `close`, `tick_volume`, `spread`, `real_volume`. Returns `None` in case of an error.

### Note

- Only data whose date is less than or equal to the specified date will be returned.
- MetaTrader 5 terminal provides bars only within the history available to a user on charts (set in "Max. bars in chart").
- **Important:** Python uses local time zone when creating `datetime` objects, while MT5 stores time in UTC. Create `datetime` in UTC time for functions that use time.

### Example

```python
from datetime import datetime
import MetaTrader5 as mt5
import pandas as pd
import pytz

pd.set_option('display.max_columns', 500)
pd.set_option('display.width', 1500)

# establish connection to MetaTrader 5 terminal
if not mt5.initialize():
    print("initialize() failed, error code =", mt5.last_error())
    quit()

# set time zone to UTC
timezone = pytz.timezone("Etc/UTC")
# create 'datetime' object in UTC time zone to avoid the implementation of a local time zone offset
utc_from = datetime(2020, 1, 10, tzinfo=timezone)
# get 10 EURUSD H4 bars starting from 01.10.2020 in UTC time zone
rates = mt5.copy_rates_from("EURUSD", mt5.TIMEFRAME_H4, utc_from, 10)

# shut down connection to the MetaTrader 5 terminal
mt5.shutdown()

# display each element of obtained data in a new line
print("Display obtained data 'as is'")
for rate in rates:
    print(rate)

# create DataFrame out of the obtained data
rates_frame = pd.DataFrame(rates)
# convert time in seconds into the datetime format
rates_frame['time'] = pd.to_datetime(rates_frame['time'], unit='s')

# display data
print("\nDisplay dataframe with data")
print(rates_frame)
```

---

## 17. copy_rates_from_pos

Get bars from the MetaTrader 5 terminal starting from the specified index.

### Signature

```python
copy_rates_from_pos(
    symbol,         # symbol name
    timeframe,      # timeframe
    start_pos,      # initial bar index
    count           # number of bars
)
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `symbol` | str | **Required** | Financial instrument name, e.g. "EURUSD". |
| `timeframe` | int | **Required** | Timeframe from the [TIMEFRAME](#timeframe-enumeration) enumeration. |
| `start_pos` | int | **Required** | Initial index of the bar. Numbering goes from present to past (0 = current bar). |
| `count` | int | **Required** | Number of bars to receive. |

### Return Value

Returns bars as a numpy array with columns: `time`, `open`, `high`, `low`, `close`, `tick_volume`, `spread`, `real_volume`. Returns `None` in case of an error.

### Example

```python
from datetime import datetime
import MetaTrader5 as mt5
import pandas as pd

pd.set_option('display.max_columns', 500)
pd.set_option('display.width', 1500)

# establish connection to MetaTrader 5 terminal
if not mt5.initialize():
    print("initialize() failed, error code =", mt5.last_error())
    quit()

# get 10 GBPUSD D1 bars from the current day
rates = mt5.copy_rates_from_pos("GBPUSD", mt5.TIMEFRAME_D1, 0, 10)

# shut down connection to the MetaTrader 5 terminal
mt5.shutdown()

# create DataFrame out of the obtained data
rates_frame = pd.DataFrame(rates)
# convert time in seconds into the datetime format
rates_frame['time'] = pd.to_datetime(rates_frame['time'], unit='s')

# display data
print("\nDisplay dataframe with data")
print(rates_frame)
```

---

## 18. copy_rates_range

Get bars in the specified date range from the MetaTrader 5 terminal.

### Signature

```python
copy_rates_range(
    symbol,         # symbol name
    timeframe,      # timeframe
    date_from,      # date the bars are requested from
    date_to         # date, up to which the bars are requested
)
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `symbol` | str | **Required** | Financial instrument name, e.g. "EURUSD". |
| `timeframe` | int | **Required** | Timeframe from the [TIMEFRAME](#timeframe-enumeration) enumeration. |
| `date_from` | datetime/int | **Required** | Date the bars are requested from. Bars with open time >= date_from are returned. |
| `date_to` | datetime/int | **Required** | Date up to which bars are requested. Bars with open time <= date_to are returned. |

### Return Value

Returns bars as a numpy array with columns: `time`, `open`, `high`, `low`, `close`, `tick_volume`, `spread`, `real_volume`. Returns `None` in case of an error.

### Example

```python
from datetime import datetime
import MetaTrader5 as mt5
import pandas as pd
import pytz

pd.set_option('display.max_columns', 500)
pd.set_option('display.width', 1500)

# establish connection to MetaTrader 5 terminal
if not mt5.initialize():
    print("initialize() failed, error code =", mt5.last_error())
    quit()

# set time zone to UTC
timezone = pytz.timezone("Etc/UTC")
utc_from = datetime(2020, 1, 10, tzinfo=timezone)
utc_to = datetime(2020, 1, 11, hour=13, tzinfo=timezone)

# get bars from USDJPY M5 within the interval
rates = mt5.copy_rates_range("USDJPY", mt5.TIMEFRAME_M5, utc_from, utc_to)

# shut down connection to the MetaTrader 5 terminal
mt5.shutdown()

# create DataFrame out of the obtained data
rates_frame = pd.DataFrame(rates)
rates_frame['time'] = pd.to_datetime(rates_frame['time'], unit='s')
print(rates_frame.head(10))
```

---

## 19. copy_ticks_from

Get ticks from the MetaTrader 5 terminal starting from the specified date.

### Signature

```python
copy_ticks_from(
    symbol,         # symbol name
    date_from,      # date the ticks are requested from
    count,          # number of requested ticks
    flags           # combination of flags defining the type of requested ticks
)
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `symbol` | str | **Required** | Financial instrument name, e.g. "EURUSD". |
| `date_from` | datetime/int | **Required** | Date the ticks are requested from. Set by `datetime` object or seconds since 1970.01.01. |
| `count` | int | **Required** | Number of ticks to receive. |
| `flags` | int | **Required** | Flag to define tick type. See [COPY_TICKS](#copy_ticks-enumeration) enumeration. |

### Return Value

Returns ticks as a numpy array with columns: `time`, `bid`, `ask`, `last`, `volume`, `time_msc`, `flags`, `volume_real`. The `flags` value can be a combination from the [TICK_FLAG](#tick_flag-enumeration) enumeration. Returns `None` in case of an error.

### Example

```python
from datetime import datetime
import MetaTrader5 as mt5
import pandas as pd
import pytz

pd.set_option('display.max_columns', 500)
pd.set_option('display.width', 1500)

# establish connection to MetaTrader 5 terminal
if not mt5.initialize():
    print("initialize() failed, error code =", mt5.last_error())
    quit()

# set time zone to UTC
timezone = pytz.timezone("Etc/UTC")
utc_from = datetime(2020, 1, 10, tzinfo=timezone)

# request 100000 EURUSD ticks starting from 10.01.2020
ticks = mt5.copy_ticks_from("EURUSD", utc_from, 100000, mt5.COPY_TICKS_ALL)
print("Ticks received:", len(ticks))

# shut down connection to the MetaTrader 5 terminal
mt5.shutdown()

# create DataFrame out of the obtained data
ticks_frame = pd.DataFrame(ticks)
ticks_frame['time'] = pd.to_datetime(ticks_frame['time'], unit='s')
print(ticks_frame.head(10))
```

---

## 20. copy_ticks_range

Get ticks for the specified date range from the MetaTrader 5 terminal.

### Signature

```python
copy_ticks_range(
    symbol,         # symbol name
    date_from,      # date the ticks are requested from
    date_to,        # date, up to which the ticks are requested
    flags           # combination of flags defining the type of requested ticks
)
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `symbol` | str | **Required** | Financial instrument name, e.g. "EURUSD". |
| `date_from` | datetime/int | **Required** | Date the ticks are requested from. |
| `date_to` | datetime/int | **Required** | Date up to which the ticks are requested. |
| `flags` | int | **Required** | Flag to define tick type. See [COPY_TICKS](#copy_ticks-enumeration) enumeration. |

### Return Value

Returns ticks as a numpy array with columns: `time`, `bid`, `ask`, `last`, `volume`, `time_msc`, `flags`, `volume_real`. Returns `None` in case of an error.

### Example

```python
from datetime import datetime
import MetaTrader5 as mt5
import pandas as pd
import pytz

pd.set_option('display.max_columns', 500)
pd.set_option('display.width', 1500)

# establish connection to MetaTrader 5 terminal
if not mt5.initialize():
    print("initialize() failed, error code =", mt5.last_error())
    quit()

# set time zone to UTC
timezone = pytz.timezone("Etc/UTC")
utc_from = datetime(2020, 1, 10, tzinfo=timezone)
utc_to = datetime(2020, 1, 11, tzinfo=timezone)

# request AUDUSD ticks within the date range
ticks = mt5.copy_ticks_range("AUDUSD", utc_from, utc_to, mt5.COPY_TICKS_ALL)
print("Ticks received:", len(ticks))

# shut down connection to the MetaTrader 5 terminal
mt5.shutdown()

# create DataFrame out of the obtained data
ticks_frame = pd.DataFrame(ticks)
ticks_frame['time'] = pd.to_datetime(ticks_frame['time'], unit='s')
print(ticks_frame.head(10))
```

---

## 21. orders_total

Get the number of active orders.

### Signature

```python
orders_total()
```

### Parameters

None.

### Return Value

Integer value.

### Note

The function is similar to `OrdersTotal`.

### Example

```python
import MetaTrader5 as mt5

# establish connection to MetaTrader 5 terminal
if not mt5.initialize():
    print("initialize() failed, error code =", mt5.last_error())
    quit()

# check the presence of active orders
orders = mt5.orders_total()
if orders > 0:
    print("Total orders =", orders)
else:
    print("Orders not found")

# shut down connection to the MetaTrader 5 terminal
mt5.shutdown()
```

---

## 22. orders_get

Get active orders with the ability to filter by symbol or ticket.

### Signatures

```python
# Call without parameters - return active orders on all symbols
orders_get()

# Call specifying a symbol
orders_get(
    symbol="SYMBOL"     # symbol name
)

# Call specifying a group of symbols
orders_get(
    group="GROUP"       # filter for selecting orders by symbols
)

# Call specifying the order ticket
orders_get(
    ticket=TICKET       # ticket
)
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `symbol` | str | Optional | Symbol name. If specified, `ticket` parameter is ignored. |
| `group` | str | Optional | Filter for arranging a group of necessary symbols. Only orders meeting the criteria are returned. |
| `ticket` | int | Optional | Order ticket (ORDER_TICKET). |

### Return Value

Returns info as a named tuple structure (namedtuple). Returns `None` in case of an error. Use `last_error()` to get error info.

### Note

- The `group` parameter allows sorting out orders by symbols using `*` wildcards and `!` for exclusion.
- Example: `group="*, !EUR"` selects orders for all symbols then excludes those containing "EUR".

### Example

```python
import MetaTrader5 as mt5
import pandas as pd

pd.set_option('display.max_columns', 500)
pd.set_option('display.width', 1500)

# establish connection to the MetaTrader 5 terminal
if not mt5.initialize():
    print("initialize() failed, error code =", mt5.last_error())
    quit()

# display data on active orders on GBPUSD
orders = mt5.orders_get(symbol="GBPUSD")
if orders is None:
    print("No orders on GBPUSD, error code={}".format(mt5.last_error()))
else:
    print("Total orders on GBPUSD:", len(orders))
    # display all active orders
    for order in orders:
        print(order)

print()

# get the list of orders on symbols whose names contain "*GBP*"
gbp_orders = mt5.orders_get(group="*GBP*")
if gbp_orders is None:
    print("No orders with group=\"*GBP*\", error code={}".format(mt5.last_error()))
else:
    print("orders_get(group=\"*GBP*\")={}".format(len(gbp_orders)))
    # display these orders as a table using pandas.DataFrame
    df = pd.DataFrame(list(gbp_orders), columns=gbp_orders[0]._asdict().keys())
    df['time_setup'] = pd.to_datetime(df['time_setup'], unit='s')
    print(df)

# shut down connection to the MetaTrader 5 terminal
mt5.shutdown()
```

---

## 23. order_calc_margin

Return margin in the account currency to perform a specified trading operation.

### Signature

```python
order_calc_margin(
    action,     # order type (ORDER_TYPE_BUY or ORDER_TYPE_SELL)
    symbol,     # symbol name
    volume,     # volume
    price       # open price
)
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `action` | int | **Required** | Order type from [ORDER_TYPE](#order_type-enumeration) enumeration. |
| `symbol` | str | **Required** | Financial instrument name. |
| `volume` | float | **Required** | Trading operation volume. |
| `price` | float | **Required** | Open price. |

### Return Value

Real value (float) if successful, otherwise `None`. Use `last_error()` to get error info.

### Note

The function allows estimating the margin necessary for a specified order type on the current account and in the current market environment without considering current pending orders and open positions. Similar to `OrderCalcMargin`.

### Example

```python
import MetaTrader5 as mt5

# establish connection to MetaTrader 5 terminal
if not mt5.initialize():
    print("initialize() failed, error code =", mt5.last_error())
    quit()

# get account currency
account_currency = mt5.account_info().currency
print("Account currency:", account_currency)

# arrange the symbol list
symbols = ("EURUSD", "GBPUSD", "USDJPY", "USDCHF", "EURJPY", "GBPJPY")
print("Symbols to check margin:", symbols)
action = mt5.ORDER_TYPE_BUY
lot = 0.1
for symbol in symbols:
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        print(symbol, "not found, skipped")
        continue
    if not symbol_info.visible:
        print(symbol, "is not visible, trying to switch on")
        if not mt5.symbol_select(symbol, True):
            print("symbol_select({}) failed, skipped", symbol)
            continue
    ask = mt5.symbol_info_tick(symbol).ask
    margin = mt5.order_calc_margin(action, symbol, lot, ask)
    if margin != None:
        print("   {} buy {} lot margin: {} {}".format(symbol, lot, margin, account_currency))
    else:
        print("order_calc_margin failed:, error code =", mt5.last_error())

# shut down connection to the MetaTrader 5 terminal
mt5.shutdown()
```

---

## 24. order_calc_profit

Return profit in the account currency for a specified trading operation.

### Signature

```python
order_calc_profit(
    action,         # order type (ORDER_TYPE_BUY or ORDER_TYPE_SELL)
    symbol,         # symbol name
    volume,         # volume
    price_open,     # open price
    price_close     # close price
)
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `action` | int | **Required** | Order type: `ORDER_TYPE_BUY` or `ORDER_TYPE_SELL`. |
| `symbol` | str | **Required** | Financial instrument name. |
| `volume` | float | **Required** | Trading operation volume. |
| `price_open` | float | **Required** | Open price. |
| `price_close` | float | **Required** | Close price. |

### Return Value

Real value (float) if successful, otherwise `None`. Use `last_error()` to get error info.

### Note

The function allows estimating a trading operation result on the current account and in the current trading environment. Similar to `OrderCalcProfit`.

### Example

```python
import MetaTrader5 as mt5

# establish connection to MetaTrader 5 terminal
if not mt5.initialize():
    print("initialize() failed, error code =", mt5.last_error())
    quit()

# get account currency
account_currency = mt5.account_info().currency
print("Account currency:", account_currency)

# arrange the symbol list
symbols = ("EURUSD", "GBPUSD", "USDJPY")
lot = 1.0
distance = 300

for symbol in symbols:
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        print(symbol, "not found, skipped")
        continue
    if not symbol_info.visible:
        if not mt5.symbol_select(symbol, True):
            print("symbol_select({}) failed, skipped", symbol)
            continue

    point = mt5.symbol_info(symbol).point
    symbol_tick = mt5.symbol_info_tick(symbol)
    ask = symbol_tick.ask
    bid = symbol_tick.bid

    buy_profit = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, symbol, lot, ask, ask + distance * point)
    if buy_profit != None:
        print("   buy {} {} lot: profit on {} points => {} {}".format(symbol, lot, distance, buy_profit, account_currency))

    sell_profit = mt5.order_calc_profit(mt5.ORDER_TYPE_SELL, symbol, lot, bid, bid - distance * point)
    if sell_profit != None:
        print("   sell {} {} lot: profit on {} points => {} {}".format(symbol, lot, distance, sell_profit, account_currency))
    print()

# shut down connection to the MetaTrader 5 terminal
mt5.shutdown()
```

---

## 25. order_check

Check funds sufficiency for performing a required trading operation. Returns results as `MqlTradeCheckResult` structure.

### Signature

```python
order_check(
    request     # request structure
)
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `request` | dict | **Required** | `MqlTradeRequest` type structure (dict) describing a required trading action. See [TradeRequest Structure](#traderequest-structure). |

### Return Value

Check result as the `MqlTradeCheckResult` structure with fields: `retcode`, `balance`, `equity`, `profit`, `margin`, `margin_free`, `margin_level`, `comment`, `request`. The `request` field contains the trading request structure passed to `order_check()`.

### Note

Successful sending of a request does not entail that the requested trading operation will be executed successfully. Similar to `OrderCheck`.

### Example

```python
import MetaTrader5 as mt5

# establish connection to MetaTrader 5 terminal
if not mt5.initialize():
    print("initialize() failed, error code =", mt5.last_error())
    quit()

# prepare the request
symbol = "USDJPY"
symbol_info = mt5.symbol_info(symbol)
if symbol_info is None:
    print(symbol, "not found, can not call order_check()")
    mt5.shutdown()
    quit()

# if the symbol is unavailable in MarketWatch, add it
if not symbol_info.visible:
    if not mt5.symbol_select(symbol, True):
        print("symbol_select({}) failed, exit", symbol)
        mt5.shutdown()
        quit()

# prepare the request
point = mt5.symbol_info(symbol).point
request = {
    "action": mt5.TRADE_ACTION_DEAL,
    "symbol": symbol,
    "volume": 1.0,
    "type": mt5.ORDER_TYPE_BUY,
    "price": mt5.symbol_info_tick(symbol).ask,
    "sl": mt5.symbol_info_tick(symbol).ask - 100 * point,
    "tp": mt5.symbol_info_tick(symbol).ask + 100 * point,
    "deviation": 10,
    "magic": 234000,
    "comment": "python script",
    "type_time": mt5.ORDER_TIME_GTC,
    "type_filling": mt5.ORDER_FILLING_RETURN,
}

# perform the check and display the result 'as is'
result = mt5.order_check(request)
print(result)

# request the result as a dictionary and display it element by element
result_dict = result._asdict()
for field in result_dict.keys():
    print("   {}={}".format(field, result_dict[field]))
    # if this is a trading request structure, display it element by element as well
    if field == "request":
        traderequest_dict = result_dict[field]._asdict()
        for tradereq_field in traderequest_dict:
            print("       traderequest: {}={}".format(tradereq_field, traderequest_dict[tradereq_field]))

# shut down connection to the MetaTrader 5 terminal
mt5.shutdown()
```

---

## 26. order_send

Send a request to perform a trading operation from the terminal to the trade server. Similar to `OrderSend`.

### Signature

```python
order_send(
    request     # request structure
)
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `request` | dict | **Required** | `MqlTradeRequest` type structure (dict) describing the required trading action. See [TradeRequest Structure](#traderequest-structure). |

### Return Value

Execution result as the `MqlTradeResult` structure with fields: `retcode`, `deal`, `order`, `volume`, `price`, `bid`, `ask`, `comment`, `request_id`, `retcode_external`, `request`. The `request` field contains the trading request passed to `order_send()`.

### Note

A trading request passes several verification stages on the trade server. First, the validity of all the necessary request fields is checked. If there are no errors, the server accepts the order for further handling.

### Example

```python
import time
import MetaTrader5 as mt5

# establish connection to the MetaTrader 5 terminal
if not mt5.initialize():
    print("initialize() failed, error code =", mt5.last_error())
    quit()

# prepare the buy request structure
symbol = "USDJPY"
symbol_info = mt5.symbol_info(symbol)
if symbol_info is None:
    print(symbol, "not found, can not call order_check()")
    mt5.shutdown()
    quit()

# if the symbol is unavailable in MarketWatch, add it
if not symbol_info.visible:
    if not mt5.symbol_select(symbol, True):
        print("symbol_select({}) failed, exit", symbol)
        mt5.shutdown()
        quit()

lot = 0.1
point = mt5.symbol_info(symbol).point
price = mt5.symbol_info_tick(symbol).ask
deviation = 20
request = {
    "action": mt5.TRADE_ACTION_DEAL,
    "symbol": symbol,
    "volume": lot,
    "type": mt5.ORDER_TYPE_BUY,
    "price": price,
    "sl": price - 100 * point,
    "tp": price + 100 * point,
    "deviation": deviation,
    "magic": 234000,
    "comment": "python script open",
    "type_time": mt5.ORDER_TIME_GTC,
    "type_filling": mt5.ORDER_FILLING_RETURN,
}

# send a trading request
result = mt5.order_send(request)
# check the execution result
print("1. order_send(): by {} {} lots at {} with deviation={} points".format(symbol, lot, price, deviation))
if result.retcode != mt5.TRADE_RETCODE_DONE:
    print("2. order_send failed, retcode={}".format(result.retcode))
    # request the result as a dictionary and display it element by element
    result_dict = result._asdict()
    for field in result_dict.keys():
        print("   {}={}".format(field, result_dict[field]))
        if field == "request":
            traderequest_dict = result_dict[field]._asdict()
            for tradereq_field in traderequest_dict:
                print("       traderequest: {}={}".format(tradereq_field, traderequest_dict[tradereq_field]))
    print("shutdown() and quit")
    mt5.shutdown()
    quit()

print("2. order_send done, ", result)
print("   opened position with POSITION_TICKET={}".format(result.order))
print("   sleep 2 seconds before closing position #{}".format(result.order))
time.sleep(2)

# create a close request
position_id = result.order
price = mt5.symbol_info_tick(symbol).bid
deviation = 20
request = {
    "action": mt5.TRADE_ACTION_DEAL,
    "symbol": symbol,
    "volume": lot,
    "type": mt5.ORDER_TYPE_SELL,
    "position": position_id,
    "price": price,
    "deviation": deviation,
    "magic": 234000,
    "comment": "python script close",
    "type_time": mt5.ORDER_TIME_GTC,
    "type_filling": mt5.ORDER_FILLING_RETURN,
}

# send a trading request
result = mt5.order_send(request)
# check the execution result
print("3. close position #{}: sell {} {} lots at {} with deviation={} points".format(position_id, symbol, lot, price, deviation))
if result.retcode != mt5.TRADE_RETCODE_DONE:
    print("4. order_send failed, retcode={}".format(result.retcode))
    print("   result", result)
else:
    print("4. position #{} closed, {}".format(position_id, result))
    # request the result as a dictionary and display it element by element
    result_dict = result._asdict()
    for field in result_dict.keys():
        print("   {}={}".format(field, result_dict[field]))
        if field == "request":
            traderequest_dict = result_dict[field]._asdict()
            for tradereq_field in traderequest_dict:
                print("       traderequest: {}={}".format(tradereq_field, traderequest_dict[tradereq_field]))

# shut down connection to the MetaTrader 5 terminal
mt5.shutdown()
```

---

## 27. positions_total

Get the number of open positions.

### Signature

```python
positions_total()
```

### Parameters

None.

### Return Value

Integer value.

### Note

The function is similar to `PositionsTotal`.

### Example

```python
import MetaTrader5 as mt5

# establish connection to MetaTrader 5 terminal
if not mt5.initialize():
    print("initialize() failed, error code =", mt5.last_error())
    quit()

# check the presence of open positions
positions_total = mt5.positions_total()
if positions_total > 0:
    print("Total positions =", positions_total)
else:
    print("Positions not found")

# shut down connection to the MetaTrader 5 terminal
mt5.shutdown()
```

---

## 28. positions_get

Get open positions with the ability to filter by symbol or ticket.

### Signatures

```python
# Call without parameters - return open positions for all symbols
positions_get()

# Call specifying a symbol
positions_get(
    symbol="SYMBOL"     # symbol name
)

# Call specifying a group of symbols
positions_get(
    group="GROUP"       # filter for selecting positions by symbols
)

# Call specifying a position ticket
positions_get(
    ticket=TICKET       # ticket
)
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `symbol` | str | Optional | Symbol name. If specified, `ticket` parameter is ignored. |
| `group` | str | Optional | Filter for arranging a group of necessary symbols. Only positions meeting the criteria are returned. |
| `ticket` | int | Optional | Position ticket (POSITION_TICKET). |

### Return Value

Returns info as a named tuple structure (namedtuple). Returns `None` in case of an error. Use `last_error()` to get error info.

### Note

- The `group` parameter allows sorting out positions by symbols using `*` wildcards and `!` for exclusion.
- Example: `group="*, !EUR"` selects positions for all symbols then excludes those containing "EUR".

### Example

```python
import MetaTrader5 as mt5
import pandas as pd

pd.set_option('display.max_columns', 500)
pd.set_option('display.width', 1500)

# establish connection to the MetaTrader 5 terminal
if not mt5.initialize():
    print("initialize() failed, error code =", mt5.last_error())
    quit()

# get open positions on USDCHF
positions = mt5.positions_get(symbol="USDCHF")
if positions == None:
    print("No positions on USDCHF, error code={}".format(mt5.last_error()))
elif len(positions) > 0:
    print("Total positions on USDCHF =", len(positions))
    # display all open positions
    for position in positions:
        print(position)

# get the list of positions on symbols whose names contain "*USD*"
usd_positions = mt5.positions_get(group="*USD*")
if usd_positions == None:
    print("No positions with group=\"*USD*\", error code={}".format(mt5.last_error()))
elif len(usd_positions) > 0:
    print("positions_get(group=\"*USD*\")={}".format(len(usd_positions)))
    # display these positions as a table using pandas.DataFrame
    df = pd.DataFrame(list(usd_positions), columns=usd_positions[0]._asdict().keys())
    df['time'] = pd.to_datetime(df['time'], unit='s')
    df.drop(['time_update', 'time_msc', 'time_update_msc', 'external_id'], axis=1, inplace=True)
    print(df)

# shut down connection to the MetaTrader 5 terminal
mt5.shutdown()
```

---

## 29. history_orders_total

Get the number of orders in trading history within the specified interval.

### Signature

```python
history_orders_total(
    date_from,      # date the orders are requested from
    date_to         # date, up to which the orders are requested
)
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `date_from` | datetime/int | **Required** | Date the orders are requested from. Set by `datetime` object or seconds since 1970.01.01. |
| `date_to` | datetime/int | **Required** | Date up to which the orders are requested. |

### Return Value

Integer value.

### Note

The function is similar to `HistoryOrdersTotal`.

### Example

```python
from datetime import datetime
import MetaTrader5 as mt5

# establish connection to MetaTrader 5 terminal
if not mt5.initialize():
    print("initialize() failed, error code =", mt5.last_error())
    quit()

# get the number of orders in history
from_date = datetime(2020, 1, 1)
to_date = datetime.now()
history_orders = mt5.history_orders_total(from_date, datetime.now())
if history_orders > 0:
    print("Total history orders =", history_orders)
else:
    print("Orders not found in history")

# shut down connection to the MetaTrader 5 terminal
mt5.shutdown()
```

---

## 30. history_orders_get

Get orders from trading history with the ability to filter by ticket or position.

### Signatures

```python
# Call specifying a time interval
history_orders_get(
    date_from,          # date the orders are requested from
    date_to,            # date, up to which the orders are requested
    group="GROUP"       # filter for selecting orders by symbols
)

# Call specifying the order ticket
history_orders_get(
    ticket=TICKET       # order ticket
)

# Call specifying the position ticket
history_orders_get(
    position=POSITION   # position ticket
)
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `date_from` | datetime/int | Required (1st form) | Date the orders are requested from. |
| `date_to` | datetime/int | Required (1st form) | Date up to which the orders are requested. |
| `group` | str | Optional | Filter for symbol names using `*` wildcards and `!` for exclusion. |
| `ticket` | int | Optional | Order ticket to filter by. |
| `position` | int | Optional | Position ticket (ORDER_POSITION_ID) to get all related orders. |

### Return Value

Returns info as a named tuple structure (namedtuple). Returns `None` in case of an error.

### Note

- The `group` parameter uses the same wildcard/exclusion syntax as other functions.
- Example: `group="*, !EUR"` selects orders for all symbols then excludes those containing "EUR".

### Example

```python
from datetime import datetime
import MetaTrader5 as mt5
import pandas as pd

pd.set_option('display.max_columns', 500)
pd.set_option('display.width', 1500)

# establish connection to the MetaTrader 5 terminal
if not mt5.initialize():
    print("initialize() failed, error code =", mt5.last_error())
    quit()

# get the number of orders in history
from_date = datetime(2020, 1, 1)
to_date = datetime.now()

history_orders = mt5.history_orders_get(from_date, to_date, group="*GBP*")
if history_orders == None:
    print("No history orders with group=\"*GBP*\", error code={}".format(mt5.last_error()))
elif len(history_orders) > 0:
    print("history_orders_get({}, {}, group=\"*GBP*\")={}".format(from_date, to_date, len(history_orders)))

print()

# display all historical orders by a position ticket
position_id = 530218319
position_history_orders = mt5.history_orders_get(position=position_id)
if position_history_orders == None:
    print("No orders with position #{}".format(position_id))
elif len(position_history_orders) > 0:
    print("Total history orders on position #{}: {}".format(position_id, len(position_history_orders)))
    # display these orders as a table using pandas.DataFrame
    df = pd.DataFrame(list(position_history_orders), columns=position_history_orders[0]._asdict().keys())
    df['time_setup'] = pd.to_datetime(df['time_setup'], unit='s')
    df['time_done'] = pd.to_datetime(df['time_done'], unit='s')
    print(df)

# shut down connection to the MetaTrader 5 terminal
mt5.shutdown()
```

---

## 31. history_deals_total

Get the number of deals in trading history within the specified interval.

### Signature

```python
history_deals_total(
    date_from,      # date the deals are requested from
    date_to         # date, up to which the deals are requested
)
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `date_from` | datetime/int | **Required** | Date the deals are requested from. Set by `datetime` object or seconds since 1970.01.01. |
| `date_to` | datetime/int | **Required** | Date up to which the deals are requested. |

### Return Value

Integer value.

### Note

The function is similar to `HistoryDealsTotal`.

### Example

```python
from datetime import datetime
import MetaTrader5 as mt5

# establish connection to MetaTrader 5 terminal
if not mt5.initialize():
    print("initialize() failed, error code =", mt5.last_error())
    quit()

# get the number of deals in history
from_date = datetime(2020, 1, 1)
to_date = datetime.now()
deals = mt5.history_deals_total(from_date, to_date)
if deals > 0:
    print("Total deals =", deals)
else:
    print("Deals not found in history")

# shut down connection to the MetaTrader 5 terminal
mt5.shutdown()
```

---

## 32. history_deals_get

Get deals from trading history within the specified interval with the ability to filter by ticket or position.

### Signatures

```python
# Call specifying a time interval
history_deals_get(
    date_from,          # date the deals are requested from
    date_to,            # date, up to which the deals are requested
    group="GROUP"       # filter for selecting deals by symbols
)

# Call specifying the order ticket
history_deals_get(
    ticket=TICKET       # order ticket
)

# Call specifying the position ticket
history_deals_get(
    position=POSITION   # position ticket
)
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `date_from` | datetime/int | Required (1st form) | Date the deals are requested from. |
| `date_to` | datetime/int | Required (1st form) | Date up to which the deals are requested. |
| `group` | str | Optional | Filter for symbol names using `*` wildcards and `!` for exclusion. |
| `ticket` | int | Optional | Order ticket (stored in DEAL_ORDER) to get all related deals. |
| `position` | int | Optional | Position ticket (stored in DEAL_POSITION_ID) to get all related deals. |

### Return Value

Returns info as a named tuple structure (namedtuple). Returns `None` in case of an error.

### Note

- The `group` parameter uses the same wildcard/exclusion syntax as other functions.
- Example: `group="*, !EUR"` selects deals for all symbols then excludes those containing "EUR".

### Example

```python
import MetaTrader5 as mt5
from datetime import datetime
import pandas as pd

pd.set_option('display.max_columns', 500)
pd.set_option('display.width', 1500)

# establish connection to the MetaTrader 5 terminal
if not mt5.initialize():
    print("initialize() failed, error code =", mt5.last_error())
    quit()

# get the number of deals in history
from_date = datetime(2020, 1, 1)
to_date = datetime.now()

# get deals for symbols whose names contain "GBP" within a specified interval
deals = mt5.history_deals_get(from_date, to_date, group="*GBP*")
if deals == None:
    print("No deals with group=\"*GBP*\", error code={}".format(mt5.last_error()))
elif len(deals) > 0:
    print("history_deals_get({}, {}, group=\"*GBP*\")={}".format(from_date, to_date, len(deals)))

# get deals for symbols whose names contain neither "EUR" nor "GBP"
deals = mt5.history_deals_get(from_date, to_date, group="*,!*EUR*,!*GBP*")
if deals == None:
    print("No deals, error code={}".format(mt5.last_error()))
elif len(deals) > 0:
    print("history_deals_get(from_date, to_date, group=\"*,!*EUR*,!*GBP*\")=", len(deals))
    # display these deals as a table using pandas.DataFrame
    df = pd.DataFrame(list(deals), columns=deals[0]._asdict().keys())
    df['time'] = pd.to_datetime(df['time'], unit='s')
    print(df)

print("")

# get all deals related to the position #530218319
position_id = 530218319
position_deals = mt5.history_deals_get(position=position_id)
if position_deals == None:
    print("No deals with position #{}".format(position_id))
elif len(position_deals) > 0:
    print("Deals with position id #{}: {}".format(position_id, len(position_deals)))
    df = pd.DataFrame(list(position_deals), columns=position_deals[0]._asdict().keys())
    df['time'] = pd.to_datetime(df['time'], unit='s')
    print(df)

# shut down connection to the MetaTrader 5 terminal
mt5.shutdown()
```

---

## Enumerations Reference

### TIMEFRAME Enumeration

| ID | Description |
|----|-------------|
| `TIMEFRAME_M1` | 1 minute |
| `TIMEFRAME_M2` | 2 minutes |
| `TIMEFRAME_M3` | 3 minutes |
| `TIMEFRAME_M4` | 4 minutes |
| `TIMEFRAME_M5` | 5 minutes |
| `TIMEFRAME_M6` | 6 minutes |
| `TIMEFRAME_M10` | 10 minutes |
| `TIMEFRAME_M12` | 12 minutes |
| `TIMEFRAME_M15` | 15 minutes |
| `TIMEFRAME_M20` | 20 minutes |
| `TIMEFRAME_M30` | 30 minutes |
| `TIMEFRAME_H1` | 1 hour |
| `TIMEFRAME_H2` | 2 hours |
| `TIMEFRAME_H3` | 3 hours |
| `TIMEFRAME_H4` | 4 hours |
| `TIMEFRAME_H6` | 6 hours |
| `TIMEFRAME_H8` | 8 hours |
| `TIMEFRAME_H12` | 12 hours |
| `TIMEFRAME_D1` | 1 day |
| `TIMEFRAME_W1` | 1 week |
| `TIMEFRAME_MN1` | 1 month |

### ORDER_TYPE Enumeration

| ID | Description |
|----|-------------|
| `ORDER_TYPE_BUY` | Market buy order |
| `ORDER_TYPE_SELL` | Market sell order |
| `ORDER_TYPE_BUY_LIMIT` | Buy Limit pending order |
| `ORDER_TYPE_SELL_LIMIT` | Sell Limit pending order |
| `ORDER_TYPE_BUY_STOP` | Buy Stop pending order |
| `ORDER_TYPE_SELL_STOP` | Sell Stop pending order |
| `ORDER_TYPE_BUY_STOP_LIMIT` | Upon reaching the order price, Buy Limit pending order is placed at StopLimit price |
| `ORDER_TYPE_SELL_STOP_LIMIT` | Upon reaching the order price, Sell Limit pending order is placed at StopLimit price |
| `ORDER_TYPE_CLOSE_BY` | Order for closing a position by an opposite one |

### COPY_TICKS Enumeration

| ID | Description |
|----|-------------|
| `COPY_TICKS_ALL` | All ticks |
| `COPY_TICKS_INFO` | Ticks containing Bid and/or Ask price changes |
| `COPY_TICKS_TRADE` | Ticks containing Last and/or Volume price changes |

### TICK_FLAG Enumeration

| ID | Description |
|----|-------------|
| `TICK_FLAG_BID` | Bid price changed |
| `TICK_FLAG_ASK` | Ask price changed |
| `TICK_FLAG_LAST` | Last price changed |
| `TICK_FLAG_VOLUME` | Volume changed |
| `TICK_FLAG_BUY` | Last Buy price changed |
| `TICK_FLAG_SELL` | Last Sell price changed |

### TRADE_REQUEST_ACTIONS Enumeration

| ID | Description |
|----|-------------|
| `TRADE_ACTION_DEAL` | Place an order for an instant deal with the specified parameters (market order) |
| `TRADE_ACTION_PENDING` | Place an order for performing a deal at specified conditions (pending order) |
| `TRADE_ACTION_SLTP` | Change open position Stop Loss and Take Profit |
| `TRADE_ACTION_MODIFY` | Change parameters of the previously placed trading order |
| `TRADE_ACTION_REMOVE` | Remove previously placed pending order |
| `TRADE_ACTION_CLOSE_BY` | Close a position by an opposite one |

### ORDER_TYPE_FILLING Enumeration

| ID | Description |
|----|-------------|
| `ORDER_FILLING_FOK` | Fill or Kill. Order can be executed only in the specified volume. If unavailable, the order is not executed. |
| `ORDER_FILLING_IOC` | Immediate or Cancel. Execute at maximum available volume; remaining volume is canceled. |
| `ORDER_FILLING_RETURN` | Return. Used for market, limit and stop limit orders. If partially filled, remaining volume is not canceled. |

### ORDER_TYPE_TIME Enumeration

| ID | Description |
|----|-------------|
| `ORDER_TIME_GTC` | Good Till Cancelled. The order stays in the queue until manually canceled. |
| `ORDER_TIME_DAY` | The order is active only during the current trading day. |
| `ORDER_TIME_SPECIFIED` | The order is active until the specified date. |
| `ORDER_TIME_SPECIFIED_DAY` | The order is active until 23:59:59 of the specified day. |

---

### TradeRequest Structure

The `MqlTradeRequest` trading request structure (passed as a Python dict):

| Field | Description |
|-------|-------------|
| `action` | Trading operation type. Value from `TRADE_REQUEST_ACTIONS` enumeration. |
| `magic` | EA ID. Allows arranging analytical handling of trading orders. Each EA can set a unique ID. |
| `order` | Order ticket. Required for modifying pending orders. |
| `symbol` | Trading instrument name. Not required when modifying orders and closing positions. |
| `volume` | Requested volume of a deal in lots. |
| `price` | Price at which an order should be executed. |
| `stoplimit` | Price at which a pending Limit order is set when the price reaches the `price` value. |
| `sl` | Stop Loss price. |
| `tp` | Take Profit price. |
| `deviation` | Maximum acceptable deviation from the requested price, in points. |
| `type` | Order type. Value from `ORDER_TYPE` enumeration. |
| `type_filling` | Order filling type. Value from `ORDER_TYPE_FILLING` enumeration. |
| `type_time` | Order type by expiration. Value from `ORDER_TYPE_TIME` enumeration. |
| `expiration` | Pending order expiration time (for `ORDER_TIME_SPECIFIED` type). |
| `comment` | Comment to an order. |
| `position` | Position ticket. Used when changing/closing a position for clear identification. |
| `position_by` | Opposite position ticket. Used when closing a position by an opposite one. |

---

### Error Codes

Error codes returned by `last_error()`:

| Constant | Value | Description |
|----------|-------|-------------|
| `RES_S_OK` | 1 | Generic success |
| `RES_E_FAIL` | -1 | Generic fail |
| `RES_E_INVALID_PARAMS` | -2 | Invalid arguments/parameters |
| `RES_E_NO_MEMORY` | -3 | No memory condition |
| `RES_E_NOT_FOUND` | -4 | No history |
| `RES_E_INVALID_VERSION` | -5 | Invalid version |
| `RES_E_AUTH_FAILED` | -6 | Authorization failed |
| `RES_E_UNSUPPORTED` | -7 | Unsupported method |
| `RES_E_AUTO_TRADING_DISABLED` | -8 | Auto-trading disabled |
| `RES_E_INTERNAL_FAIL` | -10000 | Internal IPC general error |
| `RES_E_INTERNAL_FAIL_SEND` | -10001 | Internal IPC send failed |
| `RES_E_INTERNAL_FAIL_RECEIVE` | -10002 | Internal IPC recv failed |
| `RES_E_INTERNAL_FAIL_INIT` | -10003 | Internal IPC initialization fail |
| `RES_E_INTERNAL_FAIL_CONNECT` | -10003 | Internal IPC no ipc |
| `RES_E_INTERNAL_FAIL_TIMEOUT` | -10005 | Internal timeout |

---

> **Generated from official MQL5 documentation on 2026-02-10.**
> **Total functions documented: 32**
