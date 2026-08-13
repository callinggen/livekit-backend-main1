import os
import json
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ['https://www.googleapis.com/auth/calendar']

def main():
    creds_file = os.path.join(os.path.dirname(__file__), "credentials.json")
    token_file = os.path.join(os.path.dirname(__file__), "token.json")

    if not os.path.exists(creds_file):
        print(f"Error: {creds_file} not found!")
        return

    print("Opening browser for Google Calendar authorization on port 8080...")
    flow = InstalledAppFlow.from_client_secrets_file(
        creds_file, 
        scopes=SCOPES,
        redirect_uri="http://localhost:8080/"
    )
    
    # Run server on fixed port 8080
    creds = flow.run_local_server(port=8080)

    with open(token_file, 'w') as f:
        f.write(creds.to_json())

    print(f"SUCCESS! Authorization token saved to {token_file}")

if __name__ == "__main__":
    main()
