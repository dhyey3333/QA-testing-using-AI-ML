# The Wagtail Bakery: website requirements

Acceptance criteria for the public site and the editors' admin. Written for the
Nightshift validation run before any test was designed.

1. Each bread page shows the bread's origin, type and ingredients. For example, Baguette: origin France, type Yeast bread, ingredients Flour, Salt, Water and Yeast.
2. Searching the site for a bread by name lists that bread in the results. For example, searching for "Baguette" lists Baguette.
3. A search that matches nothing tells the visitor that no results were found.
4. Choosing a tag on the Blog shows only the posts with that tag. For example, the Yeast tag shows "Tracking Wild Yeast" but not "Desserts with Benefits".
5. Visitors can send a message through the Contact Us form (subject, name, email, purpose, message). After sending, a thank-you message confirms it was received.
6. The Contact Us form refuses an email address that is not valid, and the message is not sent.
7. Staff sign in to the admin at /admin/ with their username and password. A wrong password shows an error and does not sign them in.
