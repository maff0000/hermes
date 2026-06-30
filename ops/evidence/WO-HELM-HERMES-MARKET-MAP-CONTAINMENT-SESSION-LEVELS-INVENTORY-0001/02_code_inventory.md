# Part B — Code Inventory (market_map.py, 413 lines, + session/level modules)
Computes (all DETERMINISTIC):
- get_current_session(utc_now) -> session name from trading_windows UTC windows (asia/london/newyork/overlap_ldn_ny/off_hours)
- get_previous_day_range(instrument) -> PDH/PDL from D1 candles
- get_session_range(instrument, session) -> session high/low from H1 candles
- calculate_adr(instrument, period=20) -> Average Daily Range (deterministic stat)
- get_current_price(instrument) -> latest price
Outputs: hermes:market_map:{instrument} (legacy, via RedisPublisher, prefix hermes:)
Deps: candles (H1/D1), trading_windows + hermes_market_hours + instruments (SQL). market_hours_policy.py = deterministic
weekday open/close + maintenance window per instrument (hermes_market_hours).
NO regime_detector / ARES logic. Single textual "decision" match = "decision-daemon" (a downstream CONSUMER named in a docstring), NOT an output.
No external calendar/event dependency found. Config: trading_windows + hermes_market_hours SQL tables.
