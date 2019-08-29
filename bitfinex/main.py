import json
import logging
import time

import click
import pendulum
import pandas as pd

from db import SqliteDatabase
from utils import date_range, get_data

logger = logging.getLogger()
handler = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s %(levelname)-8s %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)

API_URL = 'https://api.bitfinex.com/v2'


def symbol_start_date(symbol):
    """
    Return the datetime when `symbol` first started trading.
    """
    with open('symbols_trading_start_days.json') as f:
        data = json.load(f)

    # objects are timestamps with milliseconds, divide
    # by 1000 to remove milliseconds
    return pendulum.from_timestamp(int(data[symbol])/1000)


def get_symbols():
    """
    curl https://api-pub.bitfinex.com/v2/tickers?symbols=ALL

    Transforms from tBTCUSD to btcusd which is due to the original code in this repo.
    """
    with open('symbols.json') as f:
        return json.load(f)
    url = 'https://api-pub.bitfinex.com/v2/tickers?symbols=ALL'
    data = get_data(url)
    df = pd.DataFrame(data)
    pair_df = df[df[0].str.contains('t\w\w\w\w\w\w')]
    pair_df[0] = pair_df[0].apply(lambda x: x[1:].lower)
    return pair_df[0].tolist()



def get_candles(symbol, start_date, end_date, timeframe='5m', limit=5000, get_earliest=False):
    """
    Return symbol candles between two dates.
    https://docs.bitfinex.com/v2/reference#rest-public-candles
    """
    if get_earliest:
        url = f'{API_URL}/candles/trade:{timeframe}:t{symbol.upper()}/hist' \
              f'?start=0&limit={limit}&sort=1'
        data = get_data(url)
        # reverse data
        data = data[::-1]
    else:
        url = f'{API_URL}/candles/trade:{timeframe}:t{symbol.upper()}/hist' \
              f'?start={start_date}&end={end_date}&limit={limit}'
        data = get_data(url)

    return data


@click.command()
@click.argument('db_path', default='bitfinex.sqlite3',
                type=click.Path(resolve_path=True))
@click.option('--debug', is_flag=True, help='Set debug mode')
def main(db_path, debug):
    if debug:
        logger.setLevel(logging.DEBUG)

    db = SqliteDatabase(path=db_path)

    symbols = get_symbols()
    logging.info(f'Found {len(symbols)} symbols')
    for i, symbol in enumerate(symbols, 1):
        # get start date for symbol
        # this is either the last entry from the db
        # or the trading start date (from json file)
        latest_candle_date = db.get_latest_candle_date(symbol)
        if latest_candle_date is None:
            logging.debug('No previous entries in db. Starting from scratch')
            get_earliest = True
            start_date = 0
            logging.info(f'{i}/{len(symbols)} | {symbol} | Processing from beginning')
        else:
            logging.debug('Found previous db entries. Resuming from latest')
            get_earliest = False
            start_date = latest_candle_date
            logging.info(f'{i}/{len(symbols)} | {symbol} | Processing from {pd.to_datetime(start_date, unit="ms", utc=True)}')

        while True:
            # add 50000 minutes in ms to the start date.
            # bitfinex is supposed to return 5000 datapoints but always returns fewer
            # probably due to not all bars having trades
            # multiply by 10 to get max number of trades
            end_date = start_date + 1000 * 5 * 60 * 1000 * 10
            logging.debug(f'{start_date} -> {end_date}')
            # returns (max) 1000 candles, one for every minute
            if get_earliest:
                candles = get_candles(symbol, start_date, end_date, get_earliest=True)
                get_earliest = False
            else:
                candles = get_candles(symbol, start_date, end_date)

            # df = pd.DataFrame(candles)
            # time_diffs = df[0].astype('int').diff().value_counts()
            # if len(time_diffs) > 1:
            #     logging.debug('WARNING: more than one time difference:')
            #     logging.debug(time_diffs)

            # end when we don't see any new data
            last_start_date = start_date
            start_date = candles[0][0]

            # seems like this modifies the original 'candles' to insert the ticker
            logging.debug(f'Fetched {len(candles)} candles')
            if candles:
                db.insert_candles(symbol, candles)

            if start_date == last_start_date:
                break

            # prevent from api rate-limiting -- 60 per minute claimed, but seems to be a little slower
            time.sleep(3)

    db.close()


if __name__ == '__main__':
    main()
