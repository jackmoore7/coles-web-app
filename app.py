from flask import Flask, render_template, request, url_for, redirect, make_response
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
BAN_DURATION_SECONDS = 300  # Ban duration (e.g., 10 minutes)

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
    per_page = 9  # Adjust based on desired cards per page

    # Read timezone from cookie
    timezone_str = request.cookies.get('timezone')
    if timezone_str:
        try:
            user_tz = ZoneInfo(timezone_str)
            app.logger.debug(f"User timezone: {timezone_str}")
        except ZoneInfoNotFoundError:
            app.logger.error(f"Invalid timezone in cookie: {timezone_str}. Defaulting to UTC.")
            user_tz = utc_tz
    else:
        # No timezone cookie set. The client-side JavaScript should have set it and reloaded.
        # In this case, default to UTC or handle gracefully
        app.logger.debug("No timezone cookie found. Defaulting to UTC.")
        user_tz = utc_tz

    # Calculate the last seven days based on user's timezone
    today = dt.now(user_tz).replace(hour=0, minute=0, second=0, microsecond=0)
    last_seven_days = [today - timedelta(days=i) for i in range(0, 7)]

    # Assign labels
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
        # Handle Date Filtering and Search via POST
        selected_date = request.form.get('date')
        search_query = request.form.get('search')
        # Redirect to GET with query parameters to support pagination
        return redirect(url_for('index', date=selected_date, search=search_query))
    else:
        # Handle GET requests with query parameters
        selected_date = request.args.get('date')
        search_query = request.args.get('search')

        query = {}

        if selected_date:
            try:
                # Convert selected date (dd/mm/yyyy) to a datetime object
                date_obj = dt.strptime(selected_date, '%d/%m/%Y')

                # Make date_obj aware with user's timezone
                date_obj = date_obj.replace(tzinfo=user_tz)

                # Define the start and end of the selected day in user's timezone
                start_day_user = date_obj
                end_day_user = start_day_user + timedelta(days=1)

                # Convert start and end of day to UTC for querying
                start_day_utc = start_day_user.astimezone(utc_tz)
                end_day_utc = end_day_user.astimezone(utc_tz)

                query["date"] = {
                    "$gte": start_day_utc,
                    "$lt": end_day_utc
                }
            except ValueError:
                # Invalid date format; optionally handle the error
                selected_date = None

        if search_query:
            # Create a case-insensitive regex for partial matching
            regex = Regex(f'.*{search_query}.*', 'i')  # 'i' for case-insensitive
            query["$or"] = [
                {"item_brand": regex},
                {"item_name": regex},
                {"item_id": regex}
            ]

        # Retrieve total count for pagination
        total_messages = coles_updates_collection.count_documents(query)
        total_pages = (total_messages + per_page - 1) // per_page

        app.logger.debug(f"Query: {query}")

        # Fetch records with pagination
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
            date_buttons=date_buttons  # Pass the list of date buttons to the template
        )

if __name__ == '__main__':
    app.run(debug=True)
