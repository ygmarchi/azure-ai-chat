from msal import PublicClientApplication

app = PublicClientApplication(
    "c12648ac-a859-4111-bf74-670736574c33",
    authority="https://login.microsoftonline.com/2cd4ff2e-c457-4901-8faf-c2dbb6119a76")

# initialize result variable to hole the token response
result = None 

# We now check the cache to see
# whether we already have some accounts that the end user already used to sign in before.
accounts = app.get_accounts()
if accounts:
    # If so, you could then somehow display these accounts and let end user choose
    print("Pick the account you want to use to proceed:")
    for a in accounts:
        print(a["username"])
    # Assuming the end user chose this one
    chosen = accounts[0]
    # Now let's try to find a token in cache for this account
    result = app.acquire_token_silent(["User.Read"], account=chosen)

if not result:
    # So no suitable token exists in cache. Let's get a new one from Azure AD.
    # This will give a code to enter in a browser
    flow = app.initiate_device_flow(scopes=["email"])
    print(flow['message'])
    result = app.acquire_token_by_device_flow(flow)

if "access_token" in result:
    print(result["access_token"])  # Yay!
else:
    print(result.get("error"))
    print(result.get("error_description"))
    print(result.get("correlation_id"))  # You may need this when reporting a bug
    
        
