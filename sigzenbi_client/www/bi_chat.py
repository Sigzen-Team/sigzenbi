from sigzenbi_client.www.ai_chat import render_chat

# Module level, not just on `context`: Frappe reads page-caching config from the MODULE
# attribute and it does NOT inherit through the delegation below.
no_cache = 1


def get_context(context):
	"""SigzenBI's BUILD chat.

	A separate ROUTE, not a mode flag: build needs an analyst seat and spends the build
	purse, while /ai_chat needs a SigzenAI licence and spends the interactive purse.
	Splitting the URL is what lets Central deny one without denying the other -- before
	this page existed, "Build with AI" pointed at the interactive frame and handed a 403
	to every analyst without a SigzenAI licence.
	"""
	return render_chat(context, "build")
