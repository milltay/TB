#!/usr/bin/env python3
"""
Paper-trading arbitrage bot between Hyperliquid and Wagyu for XMR.
Simulates trades without using real funds.
"""
 
import time
import requests
from datetime import datetime
 
# ── Configuration ──────────────────────────────────────────────────────────────
INITIAL_BALANCE = 10_000.0   # Starting simulated balance in USD
TRADE_SIZE = 500.0           # USD per trade
OPEN_THRESHOLD = 0.004       # 0.4% spread to open a position
CLOSE_THRESHOLD = 0.001      # 0.1% spread to close a position
LOOP_INTERVAL = 2            # Seconds between updates
 
HYPERLIQUID_URL = "https://api.hyperliquid.xyz/info"
WAGYU_URL = "https://api.wagyu.xyz/v1/quote"
 
# Wagyu quote: swap 100 USDC (Arbitrum) → XMR to derive a price
WAGYU_QUOTE_BODY = {
    "fromChainId": 42161,
    "toChainId": 0,
    "fromToken": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
    "toToken": "XMR",
    "fromAmount": "100000000",  # 100 USDC (6 decimals)
}
 
XMR_PICONERO_DECIMALS = 10**12
 
# ── State ──────────────────────────────────────────────────────────────────────
balance = INITIAL_BALANCE
position = None  # None | {"side": "SHORT"|"LONG", "entry_spread": float,
                 #          "hl_entry": float, "wagyu_entry": float,
                 #          "size_usd": float}
 
 
# ── API helpers ────────────────────────────────────────────────────────────────
def get_hyperliquid_price() -> float | None:
    """Fetch XMR mid-price from the Hyperliquid L2 orderbook."""
    try:
        resp = requests.post(
            HYPERLIQUID_URL,
            json={"type": "l2Book", "coin": "XMR"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        levels = data["levels"]
        best_bid = float(levels[0][0]["px"])
        best_ask = float(levels[1][0]["px"])
        return (best_bid + best_ask) / 2.0
    except Exception as e:
        print(f"[ERROR] Hyperliquid fetch failed: {e}")
        return None
 
 
def get_wagyu_price() -> float | None:
    """Fetch XMR price derived from a Wagyu USDC→XMR quote."""
    try:
        resp = requests.post(
            WAGYU_URL,
            json=WAGYU_QUOTE_BODY,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        # toAmount is in piconero (12 decimals)
        to_amount_xmr = int(data["toAmount"]) / XMR_PICONERO_DECIMALS
        from_amount_usd = float(data["fromAmountUsd"])
        if to_amount_xmr <= 0:
            print("[ERROR] Wagyu returned zero XMR amount")
            return None
        return from_amount_usd / to_amount_xmr
    except Exception as e:
        print(f"[ERROR] Wagyu fetch failed: {e}")
        return None
 
 
# ── Trading logic ──────────────────────────────────────────────────────────────
def calculate_spread(hl_price: float, wagyu_price: float) -> float:
    """Return the spread as a fraction: (hl - wagyu) / wagyu."""
    return (hl_price - wagyu_price) / wagyu_price
 
 
def open_position(side: str, spread: float, hl_price: float, wagyu_price: float) -> None:
    """Simulate opening a position."""
    global position, balance
 
    if balance < TRADE_SIZE:
        print("[WARN] Insufficient balance to open a position")
        return
 
    position = {
        "side": side,
        "entry_spread": spread,
        "hl_entry": hl_price,
        "wagyu_entry": wagyu_price,
        "size_usd": TRADE_SIZE,
    }
 
    print(f"\n>>> OPENED {side} position")
    print(f"    Entry spread: {spread * 100:+.4f}%")
    print(f"    HL entry:     ${hl_price:.4f}")
    print(f"    Wagyu entry:  ${wagyu_price:.4f}")
    print(f"    Size:         ${TRADE_SIZE:.2f}")
 
 
def close_position(current_spread: float, hl_price: float, wagyu_price: float) -> None:
    """Simulate closing the current position and book PnL."""
    global position, balance
 
    if position is None:
        return
 
    side = position["side"]
    entry_spread = position["entry_spread"]
    size_usd = position["size_usd"]
 
    # PnL based on spread convergence
    # SHORT: profit when spread narrows (entry_spread - current_spread)
    # LONG:  profit when spread widens back toward zero (current_spread - entry_spread)
    if side == "SHORT":
        pnl = (entry_spread - current_spread) * size_usd
    else:  # LONG
        pnl = (current_spread - entry_spread) * size_usd
 
    balance += pnl
 
    print(f"\n<<< CLOSED {side} position")
    print(f"    Entry spread: {entry_spread * 100:+.4f}%")
    print(f"    Exit spread:  {current_spread * 100:+.4f}%")
    print(f"    HL exit:      ${hl_price:.4f}")
    print(f"    Wagyu exit:   ${wagyu_price:.4f}")
    print(f"    PnL:          ${pnl:+.4f}")
    print(f"    Balance:      ${balance:.2f}")
 
    position = None
 
 
# ── Main loop ──────────────────────────────────────────────────────────────────
def main_loop() -> None:
    """Run the arbitrage bot continuously."""
    global balance
 
    print("=" * 60)
    print("  XMR Arbitrage Paper-Trading Bot")
    print(f"  Hyperliquid ↔ Wagyu")
    print(f"  Starting balance: ${INITIAL_BALANCE:,.2f}")
    print(f"  Trade size:       ${TRADE_SIZE:,.2f}")
    print(f"  Open threshold:   {OPEN_THRESHOLD * 100:.2f}%")
    print(f"  Close threshold:  {CLOSE_THRESHOLD * 100:.2f}%")
    print("=" * 60)
 
    while True:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
 
        hl_price = get_hyperliquid_price()
        wagyu_price = get_wagyu_price()
 
        if hl_price is None or wagyu_price is None:
            print(f"[{now}] Skipping cycle — price fetch failed")
            time.sleep(LOOP_INTERVAL)
            continue
 
        spread = calculate_spread(hl_price, wagyu_price)
 
        # Status line
        pos_str = "NONE"
        if position:
            pos_str = f"{position['side']} (entry spread {position['entry_spread'] * 100:+.4f}%)"
 
        print(
            f"[{now}] "
            f"HL: ${hl_price:.4f} | "
            f"Wagyu: ${wagyu_price:.4f} | "
            f"Spread: {spread * 100:+.4f}% | "
            f"Pos: {pos_str} | "
            f"Bal: ${balance:,.2f}"
        )
 
        # Decision logic
        if position is None:
            # Look to open
            if spread > OPEN_THRESHOLD:
                open_position("SHORT", spread, hl_price, wagyu_price)
            elif spread < -OPEN_THRESHOLD:
                open_position("LONG", spread, hl_price, wagyu_price)
        else:
            # Look to close
            if abs(spread) < CLOSE_THRESHOLD:
                close_position(spread, hl_price, wagyu_price)
 
        time.sleep(LOOP_INTERVAL)
 
 
if __name__ == "__main__":
    main_loop()
