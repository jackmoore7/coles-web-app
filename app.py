from flask import Flask, render_template, request, url_for, redirect, abort
from pymongo import MongoClient
from datetime import datetime as dt, timedelta, time
from dotenv import load_dotenv
import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import threading

load_dotenv('config.env')

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')

MONGODB_URI = os.getenv('MONGODB_URI')
client = MongoClient(MONGODB_URI)
db = client['coles']
coles_updates_collection = db['coles_updates']

utc_tz = ZoneInfo("UTC")

lock = threading.Lock()

cached_messages = None
cache_timestamp = None

def should_refresh_cache():
    """
    Determine if the cache needs to be refreshed based on current time.
    Cache should be refreshed if:
    1. Cache is empty
    2. It's past 19:00 UTC and cache was last updated before 19:00 UTC today
    """
    global cache_timestamp
    
    if cached_messages is None or cache_timestamp is None:
        return True
        
    current_time = dt.now(utc_tz)
    update_time = time(19, 0)
    
    if current_time.time() >= update_time:
        cache_day = cache_timestamp.date()
        if cache_day < current_time.date() or (
            cache_day == current_time.date() and 
            cache_timestamp.time() < update_time
        ):
            return True
            
    elif cache_timestamp < (
        current_time.replace(
            hour=19, minute=0, second=0, microsecond=0
        ) - timedelta(days=1)
    ):
        return True
        
    return False

@app.route('/', methods=['GET', 'POST'])
def index():
    messages = []
    timezone_str = request.cookies.get('timezone')
    if timezone_str:
        try:
            user_tz = ZoneInfo(timezone_str)
            app.logger.debug(f"User timezone: {timezone_str}")
        except ZoneInfoNotFoundError:
            app.logger.error(f"Invalid timezone in cookie: {timezone_str}. Defaulting to UTC.")
            user_tz = utc_tz
    else:
        app.logger.debug("No timezone cookie found. Defaulting to UTC.")
        user_tz = utc_tz

    today = dt.now(user_tz).replace(hour=0, minute=0, second=0, microsecond=0)
    last_seven_days = [today - timedelta(days=i) for i in range(0, 7)]
    date_buttons = []
    for i, date in enumerate(last_seven_days):
        if i == 0:
            label = "Today"
        elif i == 1:
            label = "Yesterday"
        else:
            label = f"{i} Days Ago"
        date_buttons.append({
            'date_str': date.strftime('%d/%m/%Y'),
            'label': label
        })

    global cached_messages, cache_timestamp
    
    cache_info = {}
    if should_refresh_cache():
        with lock:
            if should_refresh_cache():
                messages = list(coles_updates_collection.find().sort("date", -1))
                cached_messages = messages
                cache_timestamp = dt.now(utc_tz)
                cache_info = {
                    'status': 'miss',
                    'timestamp': cache_timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')
                }
    else:
        cache_info = {
            'status': 'hit',
            'timestamp': cache_timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')
        }
    
    messages = cached_messages
    total_messages = len(messages)

    for message in messages:
        if message.get("date"):
            date_obj = message["date"]
            if date_obj.tzinfo is None:
                date_obj = date_obj.replace(tzinfo=utc_tz)
            else:
                date_obj = date_obj.astimezone(utc_tz)

            message["date_iso"] = date_obj.isoformat()
            message["date_formatted_utc"] = date_obj.strftime('%d/%m/%Y %H:%M:%S UTC')
            local_date_obj = date_obj.astimezone(user_tz)
            message["date_formatted_local"] = local_date_obj.strftime('%d/%m/%Y %I:%M %p %Z')

    return render_template(
        'index.html',
        messages=messages,
        total_messages=total_messages,
        date_buttons=date_buttons,
        cache_info=cache_info
    )

@app.route('/item/<int:item_id>')
def item(item_id):
    item_records = list(coles_updates_collection.find({"item_id": item_id}).sort("date", 1))

    if not item_records:
        abort(404)

    first_record = item_records[0]
    item_brand = first_record.get('item_brand', 'Unknown Brand')
    item_name = first_record.get('item_name', 'Unknown Name')
    image_url = first_record.get('image_url', None)

    timezone_str = request.cookies.get('timezone')
    user_tz = utc_tz
    if timezone_str:
        try:
            user_tz = ZoneInfo(timezone_str)
            app.logger.debug(f"User timezone: {timezone_str}")
        except ZoneInfoNotFoundError:
            app.logger.error(f"Invalid timezone in cookie: {timezone_str}. Defaulting to UTC.")

    dates = []
    prices = []
    for record in item_records:
        if record.get("date"):
            date_obj = record["date"].replace(tzinfo=utc_tz).astimezone(user_tz)
            formatted_date = date_obj.strftime('%d/%m/%Y')
            dates.append(formatted_date)
            prices.append(record.get("price_after", 0))

    if prices:
        initial_price_before = item_records[0].get("price_before", 0)
        total_prices = [initial_price_before] + prices

        lowest_price = min(total_prices)
        highest_price = max(total_prices)

        percentage_change_extremes = ((highest_price - lowest_price) / lowest_price) * 100 if lowest_price else 0

        total_price_changes = 0
        previous_price = initial_price_before
        for current_price in prices:
            if previous_price != current_price:
                total_price_changes += 1
            previous_price = current_price
    else:
        lowest_price = highest_price = percentage_change_extremes = total_price_changes = "N/A"

    latest_record = item_records[-1]
    latest_price_before = latest_record.get("price_before", "N/A")
    latest_price_after = latest_record.get("price_after", "N/A")
    change = latest_price_after - latest_price_before if isinstance(latest_price_before, (int, float)) and isinstance(latest_price_after, (int, float)) else "N/A"
    percentage_change_latest = ((change) / latest_price_before * 100) if isinstance(change, (int, float)) and latest_price_before != 0 else "N/A"

    item_url = f"https://coles.com.au/product/{item_id}"

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return {
            'html': render_template(
                'item.html',
                item_brand=item_brand,
                item_name=item_name,
                image_url=image_url,
                dates=dates,
                prices=prices,
                lowest_price=lowest_price,
                highest_price=highest_price,
                percentage_change_extremes=percentage_change_extremes,
                total_price_changes=total_price_changes,
                latest_price_before=latest_price_before,
                latest_price_after=latest_price_after,
                change=change,
                percentage_change_latest=percentage_change_latest,
                item_id=item_id,
                item_url=item_url,
                user_tz=user_tz
            ),
            'dates': dates,
            'prices': prices
        }
    
    return render_template(
        'item.html',
        item_brand=item_brand,
        item_name=item_name,
        image_url=image_url,
        dates=dates,
        prices=prices,
        lowest_price=lowest_price,
        highest_price=highest_price,
        percentage_change_extremes=percentage_change_extremes,
        total_price_changes=total_price_changes,
        latest_price_before=latest_price_before,
        latest_price_after=latest_price_after,
        change=change,
        percentage_change_latest=percentage_change_latest,
        item_id=item_id,
        item_url=item_url,
        user_tz=user_tz
    )

@app.errorhandler(404)
def not_found_error(error):
    return render_template('error.html',
                         error_code=404,
                         error_message='Page Not Found',
                         error_description='The page you are looking for does not exist.'), 404

@app.errorhandler(500)
def internal_error(error):
    return render_template('error.html',
                         error_code=500,
                         error_message='Internal Server Error',
                         error_description='Something went wrong on our end. Please try again later.'), 500

if __name__ == '__main__':
    app.run(debug=True)
