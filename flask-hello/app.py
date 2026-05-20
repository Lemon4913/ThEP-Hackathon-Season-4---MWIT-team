from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/greet", methods=["POST"])
def greet():
    data = request.get_json()
    username = data.get("username", "").strip()
    if not username:
        return jsonify({"error": "Please enter your name!"}), 400
    return jsonify({"message": f"Hello, {username}! 👋"})

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)