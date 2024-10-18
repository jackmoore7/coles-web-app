from flask import Flask, render_template, request, url_for, redirect
from pymongo import MongoClient
from datetime import datetime as dt, timedelta
from dotenv import load_dotenv
import os
from bson.regex import Regex
from zoneinfo import ZoneInfo


from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi


# Load environment variables
load_dotenv('config.env')

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')

# MongoDB Setup
MONGODB_URI = os.getenv('MONGODB_URI')
client = MongoClient(MONGODB_URI)
db = client['coles']
coles_updates_collection = db['coles_updates']

@app.route('/', methods=['GET', 'POST'])
def index():
    selected_date = None
    search_query = None
    messages = []
    page = request.args.get('page', 1, type=int)
    per_page = 9  # Adjust based on desired cards per page

    # Calculate the last seven days
    today = dt.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
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
            'date_str': date.strftime('%Y-%m-%d'),
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
                # Convert to datetime object
                date_obj = dt.strptime(selected_date, '%Y-%m-%d')
                # Define the start and end of the selected day
                start_day = dt(date_obj.year, date_obj.month, date_obj.day)
                end_day = start_day + timedelta(days=1)
                query["date"] = {
                    "$gte": start_day,
                    "$lt": end_day
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

        # Fetch records with pagination
        messages = list(
            coles_updates_collection.find(query)
            .sort("date", -1)
            .skip((page - 1) * per_page)
            .limit(per_page)
        )

        for message in messages:
            if message.get("date"):
                message["date"] = message["date"].replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("Australia/Sydney"))

        return render_template(
            'index.html',
            messages=messages,
            selected_date=selected_date,
            search_query=search_query,
            page=page,
            total_pages=total_pages,
            date_buttons=date_buttons  # Pass the list of date buttons to the template
        )

if __name__ == '__main__':
    app.run(debug=True)