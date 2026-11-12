"""b040: places, streets, brokers and nearby castles were tagged as castles (seen on the site's Castles filter).
Castles now come from titles only, address titles don't count, a lone castle word is a place, and Properstar's
castle subtype needs CASTLE_MIN_M2 of floor space unless the title says castle."""
import pytest

import landfinder as lf


def tags(source, title, description="", title_en=None, **extra):
    return lf.special_tags(dict(source=source, title=title, title_en=title_en, description=description, **extra))


@pytest.mark.parametrize("source,title,description,title_en", [
    ("properstar", "Land for sale in Castelo, Sertã RE/MAX Portugal", "", None),     # a town
    ("properstar", "Land Plot for sale in Castelo, Brazil", "", None),
    ("olx", "Terreno, Castelo", "", None),
    ("properstar", "Land Plot for sale, 0 Chateau KERN, California", "", None),     # a house number and street
    ("properstar", "Corner lot with excellent view, road to the castle", "", None),  # the road, not the castle
    ("olx", "Ichon qalada joy sotiladi", "", "Courtyard plot for sale in Ichon castle"),
    ("zillow", "Castle And Shackleford, Batesville, AR 72501", "", None),            # an address
    ("landwatch", "NHN Castle Town, White Sulphur Springs, MT, 59645", "ghost town of Castle.", None),
    ("kijiji", "Land to be developed in La Malbaie", "face au terrain de golf du Manoir", None),
    ("landwatch", "2435 Starboard Crest Road, Morristown, TN", "Lauren Castle | Castle & Associates Real Estate", None),
    ("olx", "Terreno com 4.160m2 - Guisande", "Com o seu castelo imponente, eventos culturais", None),
    ("olx", "Moradia no Monte Castro", 'construção de moradia moderna ou um "castelo" de 3 bicos', None),
])
def test_not_a_castle(source, title, description, title_en):
    assert "castle" not in tags(source, title, description, title_en)


@pytest.mark.parametrize("title", [
    "Exceptional Old Renaissance castle of Ravières (Burgundy) - 2 hours from Paris",
    "Medieval castle in the heart of Chianti Classico",
    "Exceptional apartment of 148 m² in a medieval castle of the twelfth century",
    "Château de Penchien – Estate of character to be reinvented",
    "Under Offer: Castle for sale in Dunblane, Dunblane",
    "Castle in Vieira do Minho, Portugal",
    "Charming Manor House from 1714",
])
def test_real_castles_still_count(title):
    assert "castle" in tags("properstar", title)


def test_romanian_conac_in_an_olx_title_still_counts():
    assert "castle" in tags("olx", "Teren 11230 m.p. + Conac - necesita renovare")


def test_properstar_castle_subtype_needs_floor_space():
    assert lf.properstar_castle_title("House 58 m² with garden in Château-d'Oléron", 58) == \
        "House 58 m² with garden in Château-d'Oléron"
    assert lf.properstar_castle_title("Woodside Villa, 11 Sydenham Hill, London", None) == "Woodside Villa, 11 Sydenham Hill, London"
    assert lf.properstar_castle_title("Home for sale in Vinica, Croatia", 3888) == "Castle: Home for sale in Vinica, Croatia"
    assert lf.properstar_castle_title("Medieval castle in Umbria", 90) == "Medieval castle in Umbria"  # says so itself


def test_saved_small_subtype_castles_lose_the_prefix(rates):
    rec = lf.finalize(dict(source="properstar", title="Castle: House 3 rooms 70 m² with garden", living_m2=70,
                           price=270000, currency="EUR", url="u", country="FR"), rates)
    assert rec["title"] == "House 3 rooms 70 m² with garden"
    assert "castle" not in rec["tags"]
    big = lf.finalize(dict(source="properstar", title="Castle: Home for sale in Amer, Spain", living_m2=747,
                           price=1450000, currency="EUR", url="v", country="ES"), rates)
    assert big["title"] == "Castle: Home for sale in Amer, Spain" and "castle" in big["tags"]
