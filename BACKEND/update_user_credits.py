import sys
import json
import urllib.request

def update_credits_via_api(email: str, new_credits: int):
    api_url = "http://localhost:8000/api/admin/users"
    
    print(f"Fetching users list from {api_url}...")
    try:
        req = urllib.request.Request(api_url, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as response:
            users = json.loads(response.read().decode())
    except Exception as e:
        print(f"Error fetching users: {e}")
        return

    # Find user by email
    target_user = None
    for u in users:
        if u.get("email", "").strip().lower() == email.strip().lower():
            target_user = u
            break

    if not target_user:
        print(f"User with email '{email}' not found.")
        return

    user_id = target_user["id"]
    print(f"Found User {user_id} ({target_user.get('name')}, {email}). Current credits: {target_user.get('credits')}")

    # Send PUT request to update credits ONLY
    update_url = f"http://localhost:8000/api/admin/users/{user_id}"
    payload = json.dumps({"credits": new_credits}).encode("utf-8")
    
    print(f"\nSending PUT request to {update_url} with {{'credits': {new_credits}}}...")
    try:
        put_req = urllib.request.Request(update_url, data=payload, headers={"Content-Type": "application/json"}, method="PUT")
        with urllib.request.urlopen(put_req) as resp:
            res_data = json.loads(resp.read().decode())
            print(f"Backend API Response: {res_data.get('message')}")
            print(f"New User Credits in Backend: {res_data.get('user', {}).get('credits')}")
            print("\n[SUCCESS] The integrated backend automatically processed the credit change and triggered the email notification!")
    except Exception as e:
        print(f"Error updating user via API: {e}")

if __name__ == "__main__":
    email_target = sys.argv[1] if len(sys.argv) > 1 else "khushipanwar060@gmail.com"
    target_credits = int(sys.argv[2]) if len(sys.argv) > 2 else 15

    print(f"--- CallingGen Integrated Backend Credit Update Test ---")
    print(f"Target Email: {email_target}")
    print(f"New Credits: {target_credits}\n")

    update_credits_via_api(email_target, target_credits)
