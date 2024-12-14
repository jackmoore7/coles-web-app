from flask import Flask, render_template, request, url_for, redirect, make_response, abort
from pymongo import MongoClient
from datetime import datetime as dt, timedelta
from dotenv import load_dotenv
import os
from bson.regex import Regex
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import threading
from collections import defaultdict
import time

# Load environment variables
load_dotenv('config.env')

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')

# MongoDB Setup
MONGODB_URI = os.getenv('MONGODB_URI')
client = MongoClient(MONGODB_URI)
db = client['coles']
coles_updates_collection = db['coles_updates']

# Time Zones
utc_tz = ZoneInfo("UTC")  # UTC timezone

# In-memory storage for tracking
failed_404_counts = defaultdict(int)  # Tracks total 404s per IP
banned_ips = {}  # Stores IPs with their ban expiry times

# Lock for thread-safe operations
lock = threading.Lock()

# Configuration
MAX_TOTAL_404 = 1  # Number of 404s to trigger a ban
BAN_DURATION_SECONDS = 250  # Ban duration (e.g., 10 minutes)

def get_client_ip():
    """
    Retrieves the client's IP address, considering possible proxy headers.
    """
    if request.headers.get('X-Forwarded-For'):
        # If behind a proxy (e.g., Nginx), use the first IP in the X-Forwarded-For header
        ip = request.headers.get('X-Forwarded-For').split(',')[0].strip()
    else:
        ip = request.remote_addr
    return ip

@app.before_request
def block_banned_ips():
    """
    Blocks requests from IPs that are currently banned.
    """
    client_ip = get_client_ip()
    current_time = time.time()
    with lock:
        if client_ip in banned_ips:
            ban_expiry = banned_ips[client_ip]
            if current_time < ban_expiry:
                # IP is still banned
                remaining = int(ban_expiry - current_time)
                return make_response(
                    render_template(
                        "banned.html",
                        remaining=remaining
                    ),
                    403
                )
            else:
                # Ban has expired
                del banned_ips[client_ip]
                failed_404_counts[client_ip] = 0  # Reset the 404 count

@app.after_request
def track_404s(response):
    """
    Tracks 404 responses and bans IPs after reaching the total 404 threshold.
    """
    if response.status_code == 404:
        client_ip = get_client_ip()
        with lock:
            failed_404_counts[client_ip] += 1
            app.logger.info(f"IP {client_ip} has {failed_404_counts[client_ip]} total 404(s).")
            if failed_404_counts[client_ip] >= MAX_TOTAL_404:
                banned_until = time.time() + BAN_DURATION_SECONDS
                banned_ips[client_ip] = banned_until
                app.logger.warning(
                    f"IP {client_ip} has been temporarily banned until {time.ctime(banned_until)} "
                    f"after {failed_404_counts[client_ip]} total 404(s)."
                )
                return make_response(
                    render_template("banned.html", remaining=BAN_DURATION_SECONDS),
                    403
                )
    return response

@app.route('/', methods=['GET', 'POST'])
def index():
    selected_date = None
    search_query = None
    messages = []
    page = request.args.get('page', 1, type=int)
    per_page = 9

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

    if request.method == 'POST':
        selected_date = request.form.get('date')
        search_query = request.form.get('search')
        return redirect(url_for('index', date=selected_date, search=search_query))
    else:
        selected_date = request.args.get('date')
        search_query = request.args.get('search')

        query = {}

        if selected_date:
            try:
                date_obj = dt.strptime(selected_date, '%d/%m/%Y')

                date_obj = date_obj.replace(tzinfo=user_tz)
                start_day_user = date_obj
                end_day_user = start_day_user + timedelta(days=1)

                start_day_utc = start_day_user.astimezone(utc_tz)
                end_day_utc = end_day_user.astimezone(utc_tz)

                query["date"] = {
                    "$gte": start_day_utc,
                    "$lt": end_day_utc
                }
            except ValueError:
                selected_date = None

        if search_query:
            app.logger.debug(f"Search query: {search_query}")
            or_conditions = [
                {"item_brand": Regex(f'.*{search_query}.*', 'i')},
                {"item_name": Regex(f'.*{search_query}.*', 'i')}
            ]

            sample_doc = coles_updates_collection.find_one()
            if sample_doc and 'item_id' in sample_doc:
                app.logger.debug(f"Sample item_id: {sample_doc['item_id']} (type: {type(sample_doc['item_id'])})")

            try:
                search_number = int(search_query)
                or_conditions.append({"item_id": search_number})
                app.logger.debug(f"Added numeric item_id condition with value: {search_number}")
            except ValueError:
                app.logger.debug("Search query is not a number, skipping numeric item_id condition.")

            query["$or"] = or_conditions

        # if search_query:
        #     # Create a case-insensitive regex for partial matching
        #     regex = Regex(f'.*{search_query}.*', 'i')  # 'i' for case-insensitive
        #     query["$or"] = [
        #         {"item_brand": regex},
        #         {"item_name": regex},
        #         {"item_id": regex}
        #     ]
        total_messages = coles_updates_collection.count_documents(query)
        total_pages = (total_messages + per_page - 1) // per_page

        app.logger.debug(f"Query: {query}")

        messages = list(
            coles_updates_collection.find(query)
            .sort("date", -1)
            .skip((page - 1) * per_page)
            .limit(per_page)
        )

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
            selected_date=selected_date,
            search_query=search_query,
            page=page,
            total_pages=total_pages,
            date_buttons=date_buttons
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

if __name__ == '__main__':
    app.run(debug=True)
