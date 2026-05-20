from flask import Flask, render_template, jsonify, request
from datetime import datetime

app = Flask(__name__)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/greet", methods=["POST"])
def greet():
    data = request.json
    name = data.get("name", "World").strip() or "World"
    now = datetime.now().strftime("%H:%M:%S")
    return jsonify({
        "message": f"Hello, {name}!",
        "time": now
    })

if __name__ == "__main__":
    app.run(debug=True)
