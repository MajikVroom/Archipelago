from typing import List
from typing import Dict
from typing import TextIO

from BaseClasses import Region, Tutorial, ItemClassification
from worlds.AutoWorld import WebWorld, World
from .Items import MMRItem, item_data_table, item_table, code_to_item_table
from .Locations import MMRLocation, location_data_table, location_table, code_to_location_table, locked_locations
from .Options import MMROptions
from .Regions import region_data_table, get_exit
from .Rules import *
from .NormalRules import *
from .Constants import default_shop_prices

class MMRWebWorld(WebWorld):
    # ~ theme = "partyTime"
    
    setup_en = Tutorial(
        tutorial_name="Start Guide",
        description="A guide to playing Majora's Mask Recompiled in Archipelago.",
        language="English",
        file_name="guide_en.md",
        link="guide/en",
        authors=["LittleCube", "ThatHypedPerson", "PixelShake92", "Muervo_"]
    )
    
    tutorials = [setup_en]


class MMRWorld(World):
    """A Zelda game we're not completely burnt out on."""

    game = "Majora's Mask Recompiled"
    data_version = 1
    web = MMRWebWorld()
    options_dataclass = MMROptions
    options = MMROptions
    location_name_to_id = location_table
    item_name_to_id = item_table
    
    prices_ints: List[int]
    prices: str

    def generate_early(self):
        # Create shop prices.
        self.prices_ints = []
        self.prices = ""

        if self.options.shopsanity.value != 0:
            price_max = 0

            if self.options.shop_prices.value == 2:
                price_max = 99
            elif self.options.shop_prices.value == 3:
                price_max = 200
            elif self.options.shop_prices.value == 4:
                price_max = 500

            # There are 34 (+2 fake) shop locations that need prices
            for i in range(0, 36):
                if self.options.shop_prices.value == 0:
                    price = default_shop_prices[i]
                else:
                    price = self.random.randint(0, price_max)
                self.prices_ints.append(price)
                self.prices += str(price) + " "

            self.prices = self.prices[:-1]
        
        # TODO: this should probably be on self rather than self.options TBH. Think I did it for plumbing simplicitly...
        self.options.selected_disabled_dungeons = []
        if self.options.disabled_dungeons.value != 0:
            disableable_dungeons = "Woodfall Temple", "Snowhead Temple", "Great Bay Temple", "Stone Tower Temple"
            self.options.selected_disabled_dungeons = self.random.sample(disableable_dungeons, self.options.disabled_dungeons.value)

        self.placed_songs = 0
        if self.options.song_shuffle.value == 0:
            raise RuntimeError("TODO: vanilla songs")

    def create_item(self, name: str) -> MMRItem:
        return MMRItem(name, item_data_table[name].type, item_data_table[name].code, self.player)

    def place(self, location, item):
        player = self.player
        mw = self.multiworld

        mw.get_location(location, player).place_locked_item(self.create_item(item))

    def create_items(self) -> None:
        mw = self.multiworld

        item_pool: List[MMRItem] = []

        mw.push_precollected(self.create_item("Ocarina of Time"))
        mw.push_precollected(self.create_item("Song of Time"))
        self.placed_songs += 1

        if self.options.swordless.value:
            item_pool.append(self.create_item("Progressive Sword"))

        if self.options.shieldless.value:
            item_pool.append(self.create_item("Progressive Shield"))
            
        if self.options.start_with_soaring.value:
            mw.push_precollected(self.create_item("Song of Soaring"))
            self.placed_songs += 1
        
        if self.options.shuffle_spiderhouse_reward.value:
            item_pool.append(self.create_item("Progressive Wallet"))

        if self.options.shuffle_regional_maps.value == 1:
            mw.push_precollected(self.create_item("Clock Town Map"))
            mw.push_precollected(self.create_item("Woodfall Map"))
            mw.push_precollected(self.create_item("Snowhead Map"))
            mw.push_precollected(self.create_item("Romani Ranch Map"))
            mw.push_precollected(self.create_item("Great Bay Map"))
            mw.push_precollected(self.create_item("Stone Tower Map"))

        if self.options.curiostity_shop_trades.value:
            item_pool.append(self.create_item("Blue Rupee"))
            item_pool.append(self.create_item("Red Rupee"))
            item_pool.append(self.create_item("Purple Rupee"))
            item_pool.append(self.create_item("Gold Rupee"))

        shp = self.options.starting_hearts.value
        if self.options.starting_hearts_are_containers_or_pieces.value == 0:
            for i in range(0, int((12 - shp)/4)):
                item_pool.append(self.create_item("Heart Container"))
            for i in range(0, (12 - shp) % 4):
                item_pool.append(self.create_item("Heart Piece"))
        else:
            for i in range(0, 12 - shp):
                item_pool.append(self.create_item("Heart Piece"))

        # REVIEW: I'm lazy, and don't want to update all of the "don't place keys/fairies if the dungeon is disabled"
        # code to offset the non-placed items by adding filler to the pool. And a previous version of disabled_dungeons
        # attempted to remove unreachable locations entirely, which required more complicated logic here (but that ran
        # into problems with client code which doesn't expect an entire dungeon's worth of checks to vanish, so I
        # had to change my approach).
        # Anyway, I wrote this auto-balancer. If there aren't enough locations to fit the entire static item pool,
        # it'll remove static filler. If there are unfilled locations, it'll create filler.

        unfilled_count = 0
        for location in mw.get_unfilled_locations(self.player):
            unfilled_count += 1

        unfilled_count = self.fill_pool_to_target(item_pool, unfilled_count)
        mw.itempool += item_pool

    def create_regions(self) -> None:
        player = self.player
        mw = self.multiworld

        # Create regions.
        for region_name in region_data_table.keys():
            region = Region(region_name, player, mw)
            mw.regions.append(region)

        # Create locations.
        for region_name, region_data in region_data_table.items():
            region = mw.get_region(region_name, player)
            region.add_locations({
                location_name: location_data.address for location_name, location_data in location_data_table.items()
                if location_data.region == region_name and location_data.can_create(self.options)
            }, MMRLocation)
            region.add_exits(region_data.connecting_regions)

        # Place locked locations.
        for location_name, location_data in locked_locations.items():
            # Ignore locations we never created.
            if not location_data.can_create(self.options):
                continue

            self.place(location_name, location_data_table[location_name].locked_item)

        if self.options.shuffle_regional_maps.value == 0:
            self.place("Tingle Clock Town Map Purchase", "Clock Town Map")
            self.place("Tingle Woodfall Map Purchase", "Woodfall Map")
            self.place("Tingle Snowhead Map Purchase", "Snowhead Map")
            self.place("Tingle Romani Ranch Map Purchase", "Romani Ranch Map")
            self.place("Tingle Great Bay Map Purchase", "Great Bay Map")
            self.place("Tingle Stone Tower Map Purchase", "Stone Tower Map")

        if self.options.shuffle_boss_remains.value == 0 or self.options.shuffle_boss_remains.value == 2:
            dungeon_reward_locations = {"Woodfall Temple" : "Woodfall Temple Odolwa's Remains", "Snowhead Temple" : "Snowhead Temple Goht's Remains",
                                        "Great Bay Temple" : "Great Bay Temple Gyorg's Remains", "Stone Tower Temple" : "Stone Tower Temple Inverted Twinmold's Remains"}
            remains_list = ["Odolwa's Remains", "Goht's Remains", "Gyorg's Remains", "Twinmold's Remains"]
            if self.options.shuffle_boss_remains.value == 2:
                self.random.shuffle(remains_list)

            # Place all of the remains in an enabled dungeon, or in the starting inventory.
            for dungeon, reward_location in dungeon_reward_locations.items():
                if self.options.dungeon_is_enabled(dungeon):
                    self.place(reward_location, remains_list.pop(0))
                else:
                    self.multiworld.push_precollected(self.create_item(remains_list.pop(0)))

        if not self.options.shuffle_spiderhouse_reward.value:
            self.place("Swamp Spider House Reward", "Mask of Truth")
            self.place("Ocean Spider House Reward", "Progressive Wallet")

        if self.options.skullsanity.value == 0:
            for i in range(0, 31):
                if i != 3:
                    self.place(code_to_location_table[0x3469420062700 | i], "Swamp Skulltula Token")
                if i != 0:
                    self.place(code_to_location_table[0x3469420062800 | i], "Ocean Skulltula Token")
                

        if not self.options.shuffle_great_fairy_rewards.value:
            self.place("North Clock Town Great Fairy Reward", "Progressive Magic")
            self.place("North Clock Town Great Fairy Reward (Has Transformation Mask)", "Great Fairy Mask")

            # Great fairies are only accessible (and allowed to have progressive items) if their dungeon is enabled,
            # or their fairies can be found outside their dungeon.
            # REVIEW: If a great fairy is inaccessible, should their items go into the pool? Because right now they're effectively deleted.
            if self.options.fairysanity or self.options.dungeon_is_enabled("Woodfall Temple"):
                self.place("Woodfall Great Fairy Reward", "Great Spin Attack")
            if self.options.fairysanity or self.options.dungeon_is_enabled("Snowhead Temple"):
                self.place("Snowhead Great Fairy Reward", "Progressive Magic")
            if self.options.fairysanity or self.options.dungeon_is_enabled("Great Bay Temple"):
                self.place("Great Bay Great Fairy Reward", "Double Defense")
            if self.options.fairysanity or self.options.dungeon_is_enabled("Stone Tower Temple"):
                self.place("Stone Tower Great Fairy Reward", "Great Fairy Sword")

        if not self.options.keysanity.value:
            if self.options.dungeon_is_enabled("Woodfall Temple"):
                self.place("Woodfall Temple Ledge Chest", "Small Key (Woodfall)")
            if self.options.dungeon_is_enabled("Snowhead Temple"):
                self.place("Snowhead Temple Behind Stacked Block Chest", "Small Key (Snowhead)")
                self.place("Snowhead Temple Icicle Room Snowball Chest", "Small Key (Snowhead)")
                self.place("Snowhead Temple Bridge Room Freezard Chest", "Small Key (Snowhead)")
            if self.options.dungeon_is_enabled("Great Bay Temple"):
                self.place("Great Bay Temple Caged Chest Room Underwater Chest", "Small Key (Great Bay)")
            if self.options.dungeon_is_enabled("Stone Tower Temple"):
                self.place("Stone Tower Temple Armos Room Lava Chest", "Small Key (Stone Tower)")
                self.place("Stone Tower Temple Eyegore Room Dexi Hand Ledge Chest", "Small Key (Stone Tower)")
                self.place("Stone Tower Temple Inverted Eastern Air Gust Room Switch Chest", "Small Key (Stone Tower)")
                self.place("Stone Tower Temple Inverted Death Armos Maze Chest", "Small Key (Stone Tower)")

        if not self.options.bosskeysanity.value:
            if self.options.dungeon_is_enabled("Woodfall Temple"):
                self.place("Woodfall Temple Gekko Chest", "Boss Key (Woodfall)")
            if self.options.dungeon_is_enabled("Snowhead Temple"):
                self.place("Snowhead Temple Upper Wizzrobe Chest", "Boss Key (Snowhead)")
            if self.options.dungeon_is_enabled("Great Bay Temple"):
                self.place("Great Bay Temple Mad Jellied Gekko Chest", "Boss Key (Great Bay)")
            if self.options.dungeon_is_enabled("Stone Tower Temple"):
                self.place("Stone Tower Temple Inverted Gomess Chest", "Boss Key (Stone Tower)")

        if not self.options.fairysanity.value:
            self.place("Laundry Pool Stray Fairy (Clock Town)", "Stray Fairy (Clock Town)")

            if self.options.dungeon_is_enabled("Woodfall Temple"):
                self.place("Woodfall Temple Entrance Chest SF", "Stray Fairy (Woodfall)")
                self.place("Woodfall Temple Switch Chest SF", "Stray Fairy (Woodfall)")
                self.place("Woodfall Temple Dark Room Chest SF", "Stray Fairy (Woodfall)")
                self.place("Woodfall Temple Entrance Freestanding SF", "Stray Fairy (Woodfall)")
                self.place("Woodfall Temple Deku Baba SF", "Stray Fairy (Woodfall)")
                self.place("Woodfall Temple Pot SF", "Stray Fairy (Woodfall)")
                self.place("Woodfall Temple Platform Hive SF", "Stray Fairy (Woodfall)")
                self.place("Woodfall Temple Main Room Bubble SF", "Stray Fairy (Woodfall)")
                self.place("Woodfall Temple Skulltula SF", "Stray Fairy (Woodfall)")
                self.place("Woodfall Temple Bridge Room Bubble SF", "Stray Fairy (Woodfall)")
                self.place("Woodfall Temple Bridge Room Hive SF", "Stray Fairy (Woodfall)")
                self.place("Woodfall Temple Pre-Boss Lower Right Bubble SF", "Stray Fairy (Woodfall)")
                self.place("Woodfall Temple Pre-Boss Upper Right Bubble SF", "Stray Fairy (Woodfall)")
                self.place("Woodfall Temple Pre-Boss Upper Left Bubble SF", "Stray Fairy (Woodfall)")
                self.place("Woodfall Temple Pre-Boss Pillar Bubble SF", "Stray Fairy (Woodfall)")
            if self.options.dungeon_is_enabled("Snowhead Temple"):
                self.place("Snowhead Temple Basement Switch Chest SF", "Stray Fairy (Snowhead)")
                self.place("Snowhead Temple Elevator Room Invisible Platform Chest SF", "Stray Fairy (Snowhead)")
                self.place("Snowhead Temple Stacked Block Upper Chest SF", "Stray Fairy (Snowhead)")
                self.place("Snowhead Temple Freezard Torch Room Chest SF", "Stray Fairy (Snowhead)")
                self.place("Snowhead Temple Frozen Block Upper Chest SF", "Stray Fairy (Snowhead)")
                self.place("Snowhead Temple Icicle Room Hidden Chest SF", "Stray Fairy (Snowhead)")
                self.place("Snowhead Temple Main Room Wall Chest SF", "Stray Fairy (Snowhead)")
                self.place("Snowhead Temple Bridge Room Pillar Bubble SF", "Stray Fairy (Snowhead)")
                self.place("Snowhead Temple Bridge Room Under Platform Bubble SF", "Stray Fairy (Snowhead)")
                self.place("Snowhead Temple Elevator Freestanding SF", "Stray Fairy (Snowhead)")
                self.place("Snowhead Temple Bombable Stairs Crate SF", "Stray Fairy (Snowhead)")
                self.place("Snowhead Temple Timed Switch Room Bubble SF", "Stray Fairy (Snowhead)")
                self.place("Snowhead Temple Snowmen Bubble SF", "Stray Fairy (Snowhead)")
                self.place("Snowhead Temple Dinolfos Room First SF", "Stray Fairy (Snowhead)")
                self.place("Snowhead Temple Dinolfos Room Second SF", "Stray Fairy (Snowhead)")
            if self.options.dungeon_is_enabled("Great Bay Temple"):
                self.place("Great Bay Temple Entrance Torches Chest SF", "Stray Fairy (Great Bay)")
                self.place("Great Bay Temple Bio-Baba Hall Chest SF", "Stray Fairy (Great Bay)")
                self.place("Great Bay Temple Freezable Waterwheel Upper Chest SF", "Stray Fairy (Great Bay)")
                self.place("Great Bay Temple Freezable Waterwheel Lower Chest SF", "Stray Fairy (Great Bay)")
                self.place("Great Bay Temple Seesaw Room Chest SF", "Stray Fairy (Great Bay)")
                self.place("Great Bay Temple Room Behind Waterfall Ceiling Chest SF", "Stray Fairy (Great Bay)")
                self.place("Great Bay Temple Waterwheel Room Skulltula SF", "Stray Fairy (Great Bay)")
                self.place("Great Bay Temple Waterwheel Room Bubble SF", "Stray Fairy (Great Bay)")
                self.place("Great Bay Temple Blender Pot SF", "Stray Fairy (Great Bay)")
                self.place("Great Bay Temple Blender Room Barrel SF", "Stray Fairy (Great Bay)")
                self.place("Great Bay Temple Before Red Valve Room Pot SF", "Stray Fairy (Great Bay)")
                self.place("Great Bay Temple Caged Chest Room Pot SF", "Stray Fairy (Great Bay)")
                self.place("Great Bay Temple Seesaw Room Underwater Barrel SF", "Stray Fairy (Great Bay)")
                self.place("Great Bay Temple Pre-Boss Room Platform Bubble SF", "Stray Fairy (Great Bay)")
                self.place("Great Bay Temple Pre-Boss Room Tunnel Bubble SF", "Stray Fairy (Great Bay)")
            if self.options.dungeon_is_enabled("Stone Tower Temple"):
                self.place("Stone Tower Temple Entrance Room Eye Switch Chest", "Stray Fairy (Stone Tower)")
                self.place("Stone Tower Temple Armos Room Upper Chest", "Stray Fairy (Stone Tower)")
                self.place("Stone Tower Temple Eyegore Room Switch Chest", "Stray Fairy (Stone Tower)")
                self.place("Stone Tower Temple Mirror Room Sun Face Chest", "Stray Fairy (Stone Tower)")
                self.place("Stone Tower Temple Mirror Room Sun Block Chest", "Stray Fairy (Stone Tower)")
                self.place("Stone Tower Temple Air Gust Room Side Chest", "Stray Fairy (Stone Tower)")
                self.place("Stone Tower Temple Air Gust Room Goron Switch Chest", "Stray Fairy (Stone Tower)")
                self.place("Stone Tower Temple Eyegore Chest", "Stray Fairy (Stone Tower)")
                self.place("Stone Tower Temple Eastern Water Room Underwater Chest", "Stray Fairy (Stone Tower)")
                self.place("Stone Tower Temple Inverted Entrance Room Sun Face Chest", "Stray Fairy (Stone Tower)")
                self.place("Stone Tower Temple Inverted Eastern Air Gust Room Frozen Switch Chest", "Stray Fairy (Stone Tower)")
                self.place("Stone Tower Temple Inverted Wizzrobe Chest", "Stray Fairy (Stone Tower)")
                self.place("Stone Tower Temple Inverted Eastern Air Gust Room Fire Chest", "Stray Fairy (Stone Tower)")
                self.place("Stone Tower Temple Entrance Room Lower Chest", "Stray Fairy (Stone Tower)")
                self.place("Stone Tower Temple After Garo Upside Down Chest", "Stray Fairy (Stone Tower)")

        sword_location = mw.get_location("Link's Inventory (Kokiri Sword)", player)
        if self.options.swordless.value:
            sword_location.item_rule = lambda item: item.name != "Progressive Sword"
        else:
            sword_location.place_locked_item(self.create_item("Progressive Sword"))

        shield_location = mw.get_location("Link's Inventory (Hero's Shield)", player)
        if self.options.shieldless.value:
            shield_location.item_rule = lambda item: item.name != "Progressive Shield"
        else:
            shield_location.place_locked_item(self.create_item("Progressive Shield"))

        shp = self.options.starting_hearts.value
        if self.options.starting_hearts_are_containers_or_pieces.value == 0:
            containers = int(shp/4) - 1
            for i in range(0, containers):
                self.place(code_to_location_table[0x34694200D0000 | i], "Heart Container")

            hearts_left = shp % 4
            for i in range(0, hearts_left):
                self.place(code_to_location_table[0x34694200D0000 | (containers + i)], "Heart Piece")

            if (shp % 4) != 0:
                for i in range(containers + hearts_left, containers + 4):
                    mw.get_location(code_to_location_table[0x34694200D0000 | i], player).item_rule = lambda item: item.name != "Heart Piece" and item.name != "Heart Container"
        else:
            for i in range(0, shp - 4):
                self.place(code_to_location_table[0x34694200D0000 | i], "Heart Piece")

            for i in range(shp - 4, 8):
                mw.get_location(code_to_location_table[0x34694200D0000 | i], player).item_rule = lambda item: item.name != "Heart Piece" and item.name != "Heart Container"

        if self.options.song_shuffle.value == 2:
            song_location_names = ["Top of Clock Tower (Song of Time)", "Clock Tower Happy Mask Salesman #1", "Romani Ranch Romani Game", "Southern Swamp Song Tablet", "Graveyard Day 1 Iron Knuckle Song",
                                "Deku Palace Monkey Song", "Twin Islands Goron Elder Request", "Great Bay Baby Zora Song", "Ikana Castle King Song", "Oath to Order"]
            song_names = ["Song of Time", "Song of Healing", "Epona's Song", "Song of Soaring", "Song of Storms", "Sonata of Awakening", "Goron Lullaby", "New Wave Bossa Nova", "Elegy of Emptiness", "Oath to Order"]
            total_song_count = len(song_names)

            # REVIEW: I'm tracking placed_songs based on the item_rule returning true. If it's possible for the song to not actually end up there, this is inaccurate.

            def song_only_item_rule(item):
                if self.placed_songs >= total_song_count:
                    return True
                if item.name in song_names:
                    self.placed_songs += 1
                    return True
                return False
            
            def no_songs_item_rule(item):
                return item.name not in song_names

            for location in mw.get_unfilled_locations(self.player):
                if location.name in song_location_names:
                    location.item_rule = song_only_item_rule
                else:
                    location.item_rule = no_songs_item_rule

        # TODO: check options to see what player starts with
        # ~ mw.get_location("Top of Clock Tower (Ocarina of Time)", player).place_locked_item(self.create_item(self.get_filler_item_name()))
        # ~ mw.get_location("Top of Clock Tower (Song of Time)", player).place_locked_item(self.create_item(self.get_filler_item_name()))

    def fill_pool_to_target(self, item_pool, target: int):
        starting_count = len(item_pool)
        total_added = 0
        total_filler = 0
        pending_static_filler = {}

        # Start by adding all the non-filler
        for name, item in item_data_table.items():
            if item.code and item.can_create(self.options):
                if item.type == ItemClassification.filler:
                    pending_static_filler[name] = item.num_exist
                    total_filler += item.num_exist
                else:
                    per_item_count = 0
                    while per_item_count < item.num_exist:
                        item_pool.append(self.create_item(name))
                        per_item_count += 1
                        total_added += 1
        
        if starting_count + total_added > target:
            raise RuntimeError("Not enough locations available for this item pool")
        
        if starting_count + total_added + total_filler <= target:
            # We can place all of the static filler without passing the target. No need to select.
            for name, num_exist in pending_static_filler.items():
                per_item_count = 0
                while per_item_count < num_exist:
                    item_pool.append(self.create_item(name))
                    per_item_count += 1
                    total_added += 1
        else:
            # Draw enough filler to reach the target, and discard the rest.
            for name in self.random.sample(list(pending_static_filler.keys()), k=(target - total_added), counts=pending_static_filler.values()):
                item_pool.append(self.create_item(name))
                total_added += 1

        # Generate dynamic filler for the rest
        for i in range(target - (starting_count + total_added)):
            item_pool.append(self.create_item(self.get_filler_item_name()))

    def get_filler_item_name(self) -> str:
        filler_items = ["Blue Rupee", "Red Rupee", "Purple Rupee", "Silver Rupee", "Gold Rupee"]
        return self.random.choice(filler_items)
        # filler_weights = (50, 25, 10, 5, 1)
        # return self.random.choices(filler_items, weights=filler_weights)[0]

    def set_rules(self) -> None:
        player = self.player
        mw = self.multiworld
        options = self.options
        prices = self.prices_ints

        # Completion condition.
        mw.completion_condition[player] = lambda state: state.has("Victory", player)

        if (self.options.logic_difficulty.value == 4):
            return

        # ~ if (self.options.logic_difficulty.value == 0):
            # ~ region_rules = get_baby_region_rules(player, options)
            # ~ location_rules = get_baby_location_rules(player, options)
        if (self.options.logic_difficulty.value == 1):
            region_rules = get_region_rules(player, options)
            location_rules = get_location_rules(player, options, prices)

        for entrance_name, rule in region_rules.items():
            entrance = mw.get_entrance(entrance_name, player)
            entrance.access_rule = rule

        for location in mw.get_locations(player):
            name = location.name

            if name not in location_rules:
                print(f"Location '{name}' does not have any logic")
            
            if self.options.skullsanity.value == 2 and (name == "Swamp Spider House Reward" or name == "Ocean Spider House Reward"):
                continue
            if name in location_rules and location_data_table[name].can_create(self.options):
                location.access_rule = location_rules[name]

    def write_spoiler_header(self, spoiler_handle: TextIO) -> None:
        if self.options.shopsanity.value:
            spoiler_handle.write("\nShop Prices:\n")
            for location, shop_id in shop_location_to_id.items():
                spoiler_handle.write(f"\n{location}: {self.prices_ints[shop_id]} Rupees")

    def fill_slot_data(self):
        shp = self.options.starting_hearts.value
        starting_containers = int(shp/4) - 1
        starting_pieces = shp % 4
        shuffled_containers = int((12 - shp)/4)
        shuffled_pieces = (12 - shp) % 4
        return {
            "skullsanity": self.options.skullsanity.value,
            "fairysanity": self.options.fairysanity.value,
            "shopsanity": self.options.shopsanity.value,                                                                
            "scrubsanity": self.options.scrubsanity.value,
            "shop_prices": self.prices,
            "shop_prices_ints": self.prices_ints,
            "cowsanity": self.options.cowsanity.value,
            "keysanity": self.options.keysanity.value,
            "bosskeysanity": self.options.bosskeysanity.value,
            "intro_checks": self.options.intro_checks.value,
            "curiostity_shop_trades": self.options.curiostity_shop_trades.value,
            "damage_multiplier": self.options.damage_multiplier.value,
            "death_behavior": self.options.death_behavior.value,
            "death_link": self.options.death_link.value,
            "camc": self.options.camc.value,
            "starting_heart_locations": 8 if self.options.starting_hearts_are_containers_or_pieces.value == 1 else starting_containers + starting_pieces + shuffled_containers + shuffled_pieces,
            "majora_remains_required": self.options.majora_remains_required.value,
            "moon_remains_required": self.options.moon_remains_required.value,
            "required_skull_tokens": self.options.required_skull_tokens.value,
            "required_stray_fairies": self.options.required_stray_fairies.value,
            "start_with_consumables": self.options.start_with_consumables.value,
            "permanent_chateau_romani": self.options.permanent_chateau_romani.value,
            "start_with_inverted_time": self.options.start_with_inverted_time.value,
            "receive_filled_wallets": self.options.receive_filled_wallets.value,
            "remains_allow_boss_warps": self.options.remains_allow_boss_warps.value,
            "magic_is_a_trap": self.options.magic_is_a_trap.value,
            "shuffle_regional_maps": self.options.shuffle_regional_maps.value,
            "shuffle_spiderhouse_reward": self.options.shuffle_spiderhouse_reward.value,
            "shuffle_great_fairy_rewards": self.options.shuffle_great_fairy_rewards.value,
            "link_tunic_color": ((self.options.link_tunic_color.value[0] & 0xFF) << 16) | ((self.options.link_tunic_color.value[1] & 0xFF) << 8) | (self.options.link_tunic_color.value[2] & 0xFF),
            "random_seed": self.random.getrandbits(32),
            "logic_difficulty": self.options.logic_difficulty.value,
            "disabled_dungeons": self.options.disabled_dungeons.value,
            "selected_disabled_dungeons": self.options.selected_disabled_dungeons
        }
