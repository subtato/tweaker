class User:
    def __init__(self, user_id, email):
        self.id = user_id
        self.email = email
        self.profile = None


def shipping_address(user):
    return user.profile.address


def display_name(user):
    if user.profile and user.profile.name:
        return user.profile.name
    return user.email.split("@")[0]
