# app.py
from flask import Flask, render_template, request
from pymongo import MongoClient
from datetime import datetime as dt, timedelta
from dotenv import load_dotenv
import os


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
    messages = []

    if request.method == 'POST':
        # Get date from form
        selected_date = request.form.get('date')
        if selected_date:
            # Convert selected_date string to datetime object
            try:
                date_obj = dt.strptime(selected_date, '%Y-%m-%d')
                # Fetch records for the selected date
                messages = list(coles_updates_collection.find({
                    "date": {
                        "$gte": dt(date_obj.year, date_obj.month, date_obj.day),
                        "$lt": dt(date_obj.year, date_obj.month, date_obj.day) + timedelta(days=1)
                    }
                }))
            except ValueError:
                # Invalid date format
                messages = []
    else:
        # Fetch all records sorted by date descending
        messages = list(coles_updates_collection.find().sort("date", -1).limit(100))

    return render_template('index.html', messages=messages, selected_date=selected_date)

if __name__ == '__main__':
    app.run(debug=True)
