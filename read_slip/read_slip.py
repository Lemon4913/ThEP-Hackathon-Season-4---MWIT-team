import anthropic
import base64
import sys
import json
from pathlib import Path

# -----------------------------------------------
# Paste your Anthropic API key here
# Get one at: https://console.anthropic.com
# -----------------------------------------------
API_KEY = ""

DEFAULT_SLIP = r"C:\Users\jengp\OneDrive\Desktop\ThEP Hackathon\ThEP-Hackathon-Season-4---MWIT-team\read_slip\1774268320330.jpg"   # default image if no argument given


def read_slip(image_path: str):
    path = Path(image_path)

    if not path.exists():
        print(f"File not found: {image_path}")
        return

    # Detect image type
    suffix = path.suffix.lower()
    media_types = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png",  ".gif": "image/gif",
        ".webp": "image/webp"
    }
    media_type = media_types.get(suffix, "image/jpeg")

    print(f"Reading slip: {image_path}")
    print("-" * 45)

    # Encode image to base64
    with open(path, "rb") as f:
        image_data = base64.standard_b64encode(f.read()).decode("utf-8")

    client = anthropic.Anthropic(api_key=API_KEY)

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_data,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "Extract information from this Thai bank transfer slip. "
                            "Return ONLY a JSON object with these fields: "
                            "sender_name, sender_bank, sender_account, "
                            "receiver_name, receiver_bank, receiver_account, "
                            "amount, fee, date, reference_code. "
                            "Use null for any field not found. No markdown, no explanation."
                        )
                    }
                ],
            }
        ],
    )

    raw = message.content[0].text.strip()

    # Parse and pretty-print
    try:
        data = json.loads(raw)

        print(f"Sender:      {data.get('sender_name', 'N/A')}")
        print(f"Sender bank: {data.get('sender_bank', 'N/A')}")
        print(f"Sender acc:  {data.get('sender_account', 'N/A')}")
        print(f"Receiver:    {data.get('receiver_name', 'N/A')}")
        print(f"Recv bank:   {data.get('receiver_bank', 'N/A')}")
        print(f"Recv acc:    {data.get('receiver_account', 'N/A')}")
        print(f"Amount:      {data.get('amount', 'N/A')} THB")
        print(f"Fee:         {data.get('fee', 'N/A')} THB")
        print(f"Date:        {data.get('date', 'N/A')}")
        print(f"Reference:   {data.get('reference_code', 'N/A')}")
        print("-" * 45)
        print("Full JSON:")
        print(json.dumps(data, indent=2, ensure_ascii=False))

    except json.JSONDecodeError:
        print("Raw response:")
        print(raw)


if __name__ == "__main__":
    image = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SLIP
    read_slip(image)