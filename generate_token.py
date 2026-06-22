from livekit import api

token = (
    api.AccessToken(
        "devkey",
        "devsecret123456789012345678901234567890"
    )
    .with_identity("user1")
    .with_name("user1")
    .with_grants(
        api.VideoGrants(
            room_join=True,
            room="local-test"
        )
    )
    .to_jwt()
)

print(token)
