"""Operation-bound dice using the source engine's HMAC rejection sampling."""

import hashlib
import hmac

from .world import canonical


def validate_dice(count, sides, modifier):
    if type(count) is not int or not 1 <= count <= 100:
        raise ValueError("dice count must be an integer from 1 to 100")
    if type(sides) is not int or not 2 <= sides <= 1_000_000:
        raise ValueError("die sides must be an integer from 2 to 1000000")
    if type(modifier) is not int or not -(2**31) <= modifier < 2**31:
        raise ValueError("modifier must be a signed 32-bit integer")


def draw(seed, turn_id, check_id, spec):
    sides = spec["sides"]
    limit = (1 << 256) - ((1 << 256) % sides)
    counter = 0
    dice = []
    while len(dice) < spec["count"]:
        message = canonical(["text-rpg-rng/1", turn_id, check_id, spec, counter]).encode("utf-8")
        number = int.from_bytes(hmac.new(seed, message, hashlib.sha256).digest(), "big")
        counter += 1
        if number < limit:
            dice.append(number % sides + 1)
    return {"dice": dice, "modifier": spec["modifier"], "total": sum(dice) + spec["modifier"]}
