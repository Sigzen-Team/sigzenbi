// Copyright (c) 2026, Parin Dave and contributors
// For license information, please see license.txt

// The "Fetch Subscription Details" button was removed on 2026-08-16.
// It wrote eleven fields, eight of which no longer exist, and everything it pulled already
// arrives on its own: Central PUSHES subscription state to API/subscription_reciver.py,
// credentials are issued at signup and rotated into SigzenBI Client Credential, and the portal
// reads live state from Central on every render. This doctype is now read-only and
// Central-owned, so there is nothing here for a person to fetch or edit.
