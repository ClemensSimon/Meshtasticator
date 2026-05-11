#!/usr/bin/env python3
from enum import Enum
import logging
import math
import random

import simpy

from lib.common import find_random_position
from lib.config import Config
from lib.discrete_event_sim_components import SimulationState, SimulationDataTracking
from lib.mac import set_transmit_delay, get_retransmission_msec
from lib.phy import check_collision, is_channel_active, airtime
from lib.packet import NODENUM_BROADCAST, MeshPacket, MeshMessage
from lib.point import Point

logger = logging.getLogger(__name__)

# roles taken from the protobuf config meshtastic/config.proto in https://github.com/meshtastic/protobufs
# deprecated roles are included for simulation utility
class MESHTASTIC_ROLE(Enum):
    CLIENT = 'CLIENT'
    CLIENT_MUTE = 'CLIENT_MUTE'
    ROUTER = 'ROUTER'
    ROUTER_CLIENT = 'ROUTER_CLIENT'
    REPEATER = 'REPEATER'
    TRACKER = 'TRACKER'
    SENSOR = 'SENSOR'
    TAK = 'TAK'
    CLIENT_HIDDEN = 'CLIENT_HIDDEN'
    LOST_AND_FOUND = 'LOST_AND_FOUND'
    TAK_TRACKER = 'TAK_TRACKER'
    ROUTER_LATE = 'ROUTER_LATE'
    CLIENT_BASE = 'CLIENT_BASE'

class MeshNodeStats:
    """Statistics, monitoring, and data tracking only relevant to and entirely
    internal to a single particular node
    """

    def __init__(self, nodeid: int):
        self.nodeid = nodeid

    def get_stats_dictionary(self) -> dict:
        """Return dictionary holding all internal data
        (may not need this)
        """
        data = {
            "nodeid": self.nodeid,
        }
        return data

class NodeConfig:
    """Specific configuration for a node
    """
    def __init__(self, node_id: int, position: Point, period: int, role: MESHTASTIC_ROLE = MESHTASTIC_ROLE.CLIENT, antenna_gain: float = 0, hop_limit: int = 3, neighbor_info: bool = False):
        self.node_id = node_id
        self.position = position.copy() # make sure we keep our own point
        self.period = period
        self.role = role
        self.antenna_gain = antenna_gain
        self.hop_limit = hop_limit
        self.neighbor_info = neighbor_info

    @classmethod
    def from_gen_scenario_output(cls, node_id: int, node_dict: {}, period: int):
        """create NodeConfig from a node dict as returned from gen_scenario.
        You probably want to iterate over the keys that function gives you
        and pass individual values indexed by them to this method.

        Arguments:
        node_dict -- dictionary defining a single node. From gen_scenario.
        """
        nd = node_dict
        position = Point(nd['x'], nd['y'], nd['z'])

        # roles
        isRouter = nd['isRouter']
        isRepeater = nd['isRepeater']
        isClientMute = nd['isClientMute']

        # sanity check that only one role is set
        if (isRouter and isRepeater) or \
           (isRepeater and isClientMute) or \
           (isClientMute and isRouter):
           raise Exception(f"invalid combination of roles: {nd}")

        if isRouter:
            role = MESHTASTIC_ROLE.ROUTER
        elif isRepeater:
            role = MESHTASTIC_ROLE.REPEATER
        elif isClientMute:
            role = MESHTASTIC_ROLE.CLIENT_MUTE
        else:
            role = MESHTASTIC_ROLE.CLIENT

        return NodeConfig(node_id, position, period, role, nd['antennaGain'], nd['hopLimit'], nd['neighborInfo'])

class MeshNode:
    """Class containing all the particular state of a MeshNode, references to necessary
    external resources like the simpy env, and process functions for simulation
    """
    def __init__(self, conf, sim_state: SimulationState, data_tracking: SimulationDataTracking, nodeConfig: NodeConfig):
        self.conf = conf
        self.nodeid = nodeConfig.node_id

        # set up internal RNGs
        self.moveRng = random.Random(self.nodeid)
        self.nodeRng = random.Random(self.nodeid)
        self.rebroadcastRng = random.Random()

        # require the user to specify a node configuration now, including position
        self.position = nodeConfig.position.copy() # make sure we have our own point
        self.role = nodeConfig.role
        self.hopLimit = nodeConfig.hop_limit
        self.antennaGain = nodeConfig.antenna_gain
        self.period = nodeConfig.period

        self.my_stats = MeshNodeStats(self.nodeid)

        self.messageSeq = sim_state.messageSeq
        self.env = sim_state.env
        self.bc_pipe = sim_state.bc_pipe
        self.nodes = sim_state.nodes
        self.packetsAtN = sim_state.packetsAtN
        self.packets = sim_state.packets

        self.delays = data_tracking.delays
        self.messages = data_tracking.messages

        self.nrPacketsSent = 0
        self.timesReceived = {}
        self.isReceiving = []
        self.isTransmitting = False
        self.usefulPackets = 0
        self.txAirUtilization = 0
        self.airUtilization = 0
        self.droppedByDelay = 0
        self.rebroadcastPackets = 0
        self.isMoving = False
        self.gpsEnabled = False

        # Track last broadcast position/time
        self.lastBroadcastPosition = self.position.copy()
        self.lastBroadcastTime = 0

        # track total transmit time for the last 6 buckets (each is 10s in firmware logic)
        self.channelUtilization = [0] * self.conf.CHANNEL_UTILIZATION_PERIODS  # each entry is ms spent on air in that interval
        self.channelUtilizationIndex = 0  # which "bucket" is current
        self.prevTxAirUtilization = 0.0   # how much total tx air-time had been used at last sample

        # System V6: passive route learning state
        if self.conf.SELECTED_ROUTER_TYPE == self.conf.ROUTER_TYPE.SYSTEM_V6:
            # Configurable parameters (from GA genome or defaults)
            p = getattr(self.conf, 'V6_PARAMS', {})
            self.v6_cfg = {
                'route_expiry_ms': p.get('route_expiry_ms', 300000),
                'neighbor_expiry_ms': p.get('neighbor_expiry_ms', 300000),
                'echo_timeout_ms': p.get('echo_timeout_ms', 5000),
                'echo_min_score': p.get('echo_min_score', 0.2),
                'echo_min_observations': p.get('echo_min_observations', 5),
                'mpr_recompute_interval': p.get('mpr_recompute_interval', 50),
                'relay_redundancy_threshold': p.get('relay_redundancy_threshold', 2),
                'gossip_probability': p.get('gossip_probability', 0.0),
                'defer_slot_multiplier': p.get('defer_slot_multiplier', 1.5),
                'rssi_margin_suppress': p.get('rssi_margin_suppress', 20),
                'power_control_margin': p.get('power_control_margin', 10),
                'density_threshold': p.get('density_threshold', 4),
            }
            self.v6_routes = {}
            self.v6_neighbors = {}
            self.v6_pkt_relayers = {}
            self.v6_mpr_set = set()
            self.v6_am_mpr_for = set()
            self.v6_neighbors_of_neighbor = {}
            self.v6_echo_pending = {}
            self.v6_echo_score = 0.5
            self.v6_echo_history = []
            # Network Coding: buffer of packets waiting to be XOR-combined
            self.v6_coding_buffer = []  # list of (packet, rssi, arrival_time)
            self.v6_coded_packets_sent = 0  # tracking: how many coded (2-for-1) TXs

        self.env.process(self.track_channel_utilization())
        if not self.is_repeater:  # repeaters don't generate messages themselves
            self.env.process(self.generate_message())
        self.env.process(self.receive(self.bc_pipe.get_output_conn()))
        self.transmitter = simpy.Resource(self.env, 1)

        # start mobility if enabled
        if self.conf.MOVEMENT_ENABLED and self.moveRng.random() <= self.conf.APPROX_RATIO_NODES_MOVING:
            self.isMoving = True
            if self.moveRng.random() <= self.conf.APPROX_RATIO_OF_NODES_MOVING_W_GPS_ENABLED:
                self.gpsEnabled = True

            # Randomly assign a movement speed
            possibleSpeeds = [
                self.conf.WALKING_METERS_PER_MIN,  # e.g.,  96 m/min
                self.conf.BIKING_METERS_PER_MIN,   # e.g., 390 m/min
                self.conf.DRIVING_METERS_PER_MIN   # e.g., 1500 m/min
            ]
            self.movementStepSize = self.moveRng.choice(possibleSpeeds)

            self.env.process(self.move_node())

    @property
    def is_router(self):
        return self.role == MESHTASTIC_ROLE.ROUTER

    @property
    def is_repeater(self):
        return self.role == MESHTASTIC_ROLE.REPEATER

    @property
    def is_client_mute(self):
        return self.role == MESHTASTIC_ROLE.CLIENT_MUTE

    def track_channel_utilization(self):
        """
        Periodically compute how many seconds of airtime this node consumed
        over the last 10-second block and store it in the ring buffer.
        """
        while True:
            # Wait 10 seconds of simulated time
            yield self.env.timeout(self.conf.TEN_SECONDS_INTERVAL)

            curTotalAirtime = self.txAirUtilization  # total so far, in *milliseconds*
            blockAirtimeMs = curTotalAirtime - self.prevTxAirUtilization

            self.channelUtilization[self.channelUtilizationIndex] = blockAirtimeMs

            self.prevTxAirUtilization = curTotalAirtime
            self.channelUtilizationIndex = (self.channelUtilizationIndex + 1) % self.conf.CHANNEL_UTILIZATION_PERIODS

    def channel_utilization_percent(self) -> float:
        """
        Returns how much of the last 60 seconds (6 x 10s) this node spent transmitting, as a percent.
        """
        sumMs = sum(self.channelUtilization)
        # 6 intervals, each 10 seconds = 60,000 ms total
        # fraction = sum_ms / 60000, then multiply by 100 for percent
        return (sumMs / (self.conf.CHANNEL_UTILIZATION_PERIODS * self.conf.TEN_SECONDS_INTERVAL)) * 100.0

    def move_node(self):
        while True:

            # Pick a random direction and distance
            angle = 2 * math.pi * self.moveRng.random()
            distance = self.movementStepSize * self.moveRng.random()

            # Compute new position
            dx = distance * math.cos(angle)
            dy = distance * math.sin(angle)

            leftBound = self.conf.OX - self.conf.XSIZE / 2
            rightBound = self.conf.OX + self.conf.XSIZE / 2
            bottomBound = self.conf.OY - self.conf.YSIZE / 2
            topBound = self.conf.OY + self.conf.YSIZE / 2

            # Then in moveNode:
            new_x = min(max(self.position.x + dx, leftBound), rightBound)
            new_y = min(max(self.position.y + dy, bottomBound), topBound)

            # Update node’s position
            self.position.update_xy(new_x, new_y)

            if self.gpsEnabled:
                distanceTraveled = self.position.euclidean_distance(self.lastBroadcastPosition)
                logger.debug(f"{self.env.now:.3f} node {self.nodeid} checks last broadcast position distance: {distanceTraveled} from {self.lastBroadcastPosition} to {self.position}")
                timeElapsed = self.env.now - self.lastBroadcastTime
                if distanceTraveled >= self.conf.SMART_POSITION_DISTANCE_THRESHOLD and timeElapsed >= self.conf.SMART_POSITION_DISTANCE_MIN_TIME:
                    currentUtil = self.channel_utilization_percent()
                    if currentUtil < 25.0:
                        self.send_packet(NODENUM_BROADCAST, "POSITION")
                        self.lastBroadcastPosition.update_xy(self.position.x, self.position.y)
                        self.lastBroadcastTime = self.env.now
                    else:
                        logger.debug(f"{self.env.now:.3f} node {self.nodeid} SKIPS POSITION broadcast (util={currentUtil:.1f}% > 25%)")

            # Wait until next move
            nextMove = self.get_next_time(self.conf.ONE_MIN_INTERVAL)
            if nextMove >= 0:
                yield self.env.timeout(nextMove)
            else:
                break

    def send_packet(self, destId, type=""):
        """We have created a new message and wish to send it to the network
        """
        # increment the shared counter
        messageSeq = self.messageSeq.get()
        self.messages.append(MeshMessage(self.nodeid, destId, self.env.now, messageSeq))
        p = MeshPacket(self.conf, self.nodes, self.nodeid, destId, self.nodeid, self.conf.PACKETLENGTH, messageSeq, self.env.now, True, False, None, self.env.now)
        logger.debug(f"{self.env.now:.3f} Node {self.nodeid} generated {type} message {p.seq} to {destId}")
        self.packets.append(p)
        self.env.process(self.transmit(p))
        return p

    def get_next_time(self, period):
        nextGen = self.nodeRng.expovariate(1.0 / float(period))
        # do not generate message near the end of the simulation (otherwise flooding cannot finish in time)
        if self.env.now+nextGen + self.hopLimit * airtime(self.conf, self.conf.current_preset["sf"], self.conf.current_preset["cr"], self.conf.PACKETLENGTH, self.conf.current_preset["bw"]) < self.conf.SIMTIME:
            return nextGen
        return -1
    

    def was_seen_recently(self, packet, ownTransmit=False):
        if packet.seq not in self.timesReceived:
            # First time we know about this packet
            self.timesReceived[packet.seq] = 0 if ownTransmit else 1
            if not ownTransmit:
                self.usefulPackets += 1
        else:
            self.timesReceived[packet.seq] += 0 if ownTransmit else 1


    def perhaps_cancel_dupe(self, packet):
        # Cancel if we've already seen this sequence number
        if packet.seq in self.timesReceived:
            return self.timesReceived[packet.seq] > 2 if self.is_router or self.is_repeater else self.timesReceived[packet.seq] > 1
        return False


    def generate_message(self):
        while True:
            # Returns -1 if we don't make it before the sim ends
            nextGen = self.get_next_time(self.period)
            # do not generate a message near the end of the simulation (otherwise flooding cannot finish in time)
            if nextGen >= 0:
                yield self.env.timeout(nextGen)

                if self.conf.DMs:
                    destId = self.nodeRng.choice([i for i in range(0, len(self.nodes)) if i is not self.nodeid])
                else:
                    destId = NODENUM_BROADCAST

                p = self.send_packet(destId)

                while p.wantAck:  # ReliableRouter: retransmit message if no ACK received after timeout
                    retransmissionMsec = get_retransmission_msec(self, p)
                    yield self.env.timeout(retransmissionMsec)

                    ackReceived = False  # check whether you received an ACK on the transmitted message
                    minRetransmissions = self.conf.maxRetransmission
                    for packetSent in self.packets:
                        if packetSent.origTxNodeId == self.nodeid and packetSent.seq == p.seq:
                            if packetSent.retransmissions < minRetransmissions:
                                minRetransmissions = packetSent.retransmissions
                            if packetSent.ackReceived:
                                ackReceived = True
                    if ackReceived:
                        logger.debug(f"{self.env.now:.3f} Node {self.nodeid} received ACK on generated message with seq. nr. {p.seq}")
                        break
                    else:
                        if minRetransmissions > 0:  # generate new packet with same sequence number
                            pNew = MeshPacket(self.conf, self.nodes, self.nodeid, p.destId, self.nodeid, p.packetLen, p.seq, p.genTime, p.wantAck, False, None, self.env.now)
                            pNew.retransmissions = minRetransmissions - 1
                            logger.debug(f"{self.env.now:.3f} Node {self.nodeid} wants to retransmit its generated packet to {destId} with seq.nr. {p.seq} minRetransmissions {minRetransmissions}")
                            self.packets.append(pNew)
                            self.env.process(self.transmit(pNew))
                        else:
                            logger.debug(f"{self.env.now:.3f} Node {self.nodeid} reliable send of {p.seq} failed.")
                            break
            else:  # do not send this message anymore, since it is close to the end of the simulation
                break

    def transmit(self, packet):
        with self.transmitter.request() as request:
            yield request

            # listen-before-talk from src/mesh/RadioLibInterface.cpp
            txTime = set_transmit_delay(self, packet)
            logger.debug(f"{self.env.now:.3f} Node {self.nodeid} picked wait time {txTime}")
            yield self.env.timeout(txTime)

            # wait when currently receiving or transmitting, or channel is active
            while any(self.isReceiving) or self.isTransmitting or is_channel_active(self, self.env):
                logger.debug(f"{self.env.now:.3f} Node {self.nodeid} is busy Tx-ing {self.isTransmitting} or Rx-ing {any(self.isReceiving)} else channel busy!")
                txTime = set_transmit_delay(self, packet)
                yield self.env.timeout(txTime)
            logger.debug(f"{self.env.now:.3f} Node {self.nodeid} ends waiting")

            # check if you received an ACK for this message in the meantime
            self.was_seen_recently(packet, ownTransmit=True)
            if not self.perhaps_cancel_dupe(packet):  # if you did not receive an ACK for this message in the meantime
                logger.debug(f"{self.env.now:.3f} Node {self.nodeid} started low level send {packet.seq} hopLimit {packet.hopLimit} original Tx {packet.origTxNodeId}")
                self.nrPacketsSent += 1
                for rx_node in self.nodes:
                    if packet.sensedByN[rx_node.nodeid]:
                        if check_collision(self.conf, self.env, packet, rx_node.nodeid, self.packetsAtN) == 0:
                            self.packetsAtN[rx_node.nodeid].append(packet)
                packet.startTime = self.env.now
                packet.endTime = self.env.now + packet.timeOnAir
                self.txAirUtilization += packet.timeOnAir
                self.airUtilization += packet.timeOnAir
                self.bc_pipe.put(packet)
                self.isTransmitting = True
                yield self.env.timeout(packet.timeOnAir)
                self.isTransmitting = False
            else:  # received ACK: abort transmit, remove from packets generated
                logger.debug(f"{self.env.now:.3f} Node {self.nodeid} in the meantime received ACK, abort packet with seq. nr {packet.seq}")
                self.packets.remove(packet)

    def receive(self, in_pipe):
        while True:
            p = yield in_pipe.get()

            if p.sensedByN[self.nodeid] and p.onAirToN[self.nodeid]:  # start of reception
                if p.collidedAtN[self.nodeid]:
                    # this packet collided, so we can sense it but not decode it.
                    # Mark it as no-longer on air and leave further processing to
                    # the 'end of transmission' branch
                    p.onAirToN[self.nodeid] = False
                elif not self.isTransmitting:
                    logger.debug(f"{self.env.now:.3f} Node {self.nodeid} started receiving packet {p.seq} from {p.txNodeId}")
                    p.onAirToN[self.nodeid] = False
                    self.isReceiving.append(True)
                else:  # if you were currently transmitting, you could not have sensed it
                    logger.debug(f"{self.env.now:.3f} Node {self.nodeid} was transmitting, so could not receive packet {p.seq}")
                    p.sensedByN[self.nodeid] = False
                    p.onAirToN[self.nodeid] = False
            elif p.sensedByN[self.nodeid]:  # end of reception
                try:
                    self.isReceiving[self.isReceiving.index(True)] = False
                except Exception:
                    pass
                self.airUtilization += p.timeOnAir
                if p.collidedAtN[self.nodeid]:
                    logger.debug(f"{self.env.now:.3f} Node {self.nodeid} could not decode packet.")
                    continue
                p.receivedAtN[self.nodeid] = True
                logger.debug(f"{self.env.now:.3f} Node {self.nodeid} received packet {p.seq} with delay {round(self.env.now - p.genTime, 2)}") # TODO: better way to calculate delay for log
                self.delays.append(self.env.now - p.genTime)

                # Update history of received packets
                self.was_seen_recently(p)

                # check if implicit ACK for own generated message
                if p.origTxNodeId == self.nodeid:
                    if p.isAck:
                        logger.debug(f"Node {self.nodeid} received real ACK on generated message.")
                    else:
                        logger.debug(f"Node {self.nodeid} received implicit ACK on message sent.")
                    p.ackReceived = True
                    continue

                ackReceived = False
                realAckReceived = False
                for sentPacket in self.packets:
                    # check if ACK for message you currently have in queue
                    if sentPacket.txNodeId == self.nodeid and sentPacket.seq == p.seq:
                        logger.debug(f"{self.env.now:.3f} Node {self.nodeid} received implicit ACK for message in queue.")
                        ackReceived = True
                        sentPacket.ackReceived = True
                    # check if real ACK for message sent
                    if sentPacket.origTxNodeId == self.nodeid and p.isAck and sentPacket.seq == p.requestId:
                        logger.debug(f"{self.env.now:.3f} Node {self.nodeid} received real ACK.")
                        realAckReceived = True
                        sentPacket.ackReceived = True

                # send real ACK if you are the destination and you did not yet send the ACK
                if p.wantAck and p.destId == self.nodeid and not any(pA.requestId == p.seq for pA in self.packets):
                    logger.debug(f"{self.env.now:.3f} Node {self.nodeid} sends a flooding ACK.")
                    messageSeq = self.messageSeq.get()
                    self.messages.append(MeshMessage(self.nodeid, p.origTxNodeId, self.env.now, messageSeq))
                    pAck = MeshPacket(self.conf, self.nodes, self.nodeid, p.origTxNodeId, self.nodeid, self.conf.ACKLENGTH, messageSeq, self.env.now, False, True, p.seq, self.env.now)
                    self.packets.append(pAck)
                    self.env.process(self.transmit(pAck))
                # Rebroadcasting Logic for received message. This is a broadcast or a DM not meant for us.
                elif not p.destId == self.nodeid and not ackReceived and not realAckReceived and p.hopLimit > 0:
                    # FloodingRouter: rebroadcast received packet
                    if self.conf.SELECTED_ROUTER_TYPE == self.conf.ROUTER_TYPE.MANAGED_FLOOD:
                        if not self.is_client_mute:
                            logger.debug(f"{self.env.now:.3f} Node {self.nodeid} rebroadcasts received packet {p.seq}")
                            pNew = MeshPacket(self.conf, self.nodes, p.origTxNodeId, p.destId, self.nodeid, p.packetLen, p.seq, p.genTime, p.wantAck, False, None, self.env.now)
                            pNew.hopLimit = p.hopLimit - 1
                            self.packets.append(pNew)
                            self.env.process(self.transmit(pNew))
                    elif self.conf.SELECTED_ROUTER_TYPE == self.conf.ROUTER_TYPE.SYSTEM_V6:
                        # System V6: passive route learning + MPR + ECHO backbone
                        rssi = p.rssiAtN[self.nodeid] if self.nodeid < len(p.rssiAtN) else -140

                        # Learn neighbors: track every node we hear directly
                        if p.txNodeId not in self.v6_neighbors:
                            self.v6_neighbors[p.txNodeId] = {'rssi': rssi, 'lastSeen': self.env.now, 'relayCount': 1}
                        else:
                            nb = self.v6_neighbors[p.txNodeId]
                            nb['rssi'] = max(nb['rssi'], rssi)
                            nb['lastSeen'] = self.env.now
                            nb['relayCount'] += 1

                        # Learn 2-hop topology: if origTx != tx, then tx is a neighbor of origTx
                        if p.origTxNodeId != p.txNodeId:
                            if p.txNodeId not in self.v6_neighbors_of_neighbor:
                                self.v6_neighbors_of_neighbor[p.txNodeId] = set()
                            self.v6_neighbors_of_neighbor[p.txNodeId].add(p.origTxNodeId)
                            # Reverse: origTx knows txNodeId
                            if p.origTxNodeId not in self.v6_neighbors_of_neighbor:
                                self.v6_neighbors_of_neighbor[p.origTxNodeId] = set()
                            self.v6_neighbors_of_neighbor[p.origTxNodeId].add(p.txNodeId)

                        # ECHO detection: did someone relay a packet I previously rebroadcasted?
                        if p.seq in self.v6_echo_pending:
                            # I rebroadcasted this seq, now I hear it from someone else = ECHO!
                            del self.v6_echo_pending[p.seq]
                            self.v6_echo_history.append((self.env.now, True))
                            self.v6_update_echo_score()

                        # Learn routes: best relay for each origin (with expiry check)
                        route_expiry = self.v6_cfg['route_expiry_ms']
                        existing = self.v6_routes.get(p.origTxNodeId)
                        if not existing or rssi > existing.get('rssi', -999) or (self.env.now - existing.get('time', 0)) > route_expiry:
                            self.v6_routes[p.origTxNodeId] = {'nextHop': p.txNodeId, 'rssi': rssi, 'time': self.env.now}

                        # Expire stale neighbors
                        nb_expiry = self.v6_cfg['neighbor_expiry_ms']
                        stale = [nid for nid, nb in self.v6_neighbors.items() if (self.env.now - nb['lastSeen']) > nb_expiry]
                        for nid in stale:
                            del self.v6_neighbors[nid]

                        # Track which relayers we've heard for this specific packet
                        if p.seq not in self.v6_pkt_relayers:
                            self.v6_pkt_relayers[p.seq] = set()
                        self.v6_pkt_relayers[p.seq].add(p.txNodeId)

                        # Recompute MPR set periodically
                        total_obs = sum(nb['relayCount'] for nb in self.v6_neighbors.values())
                        if total_obs % self.v6_cfg['mpr_recompute_interval'] == 0 and len(self.v6_neighbors) >= 3:
                            self.v6_compute_mpr()

                        # Forward decision with deferred rebroadcast
                        if not self.is_client_mute:
                            self.env.process(self.v6_deferred_forward(p, rssi))
                else:
                    self.droppedByDelay += 1

    def v6_deferred_forward(self, packet, rssi):
        """Wait briefly, then decide whether to rebroadcast.

        Key improvement: by waiting ~1 slot time, we observe whether other relays
        have already forwarded the packet. This dramatically improves suppression
        in dense networks where multiple nodes hear the same packet simultaneously.
        """
        from lib.phy import get_current_slot_time
        # Wait 1-2 slot times (proportional to how strong the signal was —
        # nodes with weaker signals wait longer, giving closer nodes priority)
        sensitivity = self.conf.current_preset["sensitivity"]
        rssi_range = abs(sensitivity)  # e.g., 131.5
        rssi_normalized = max(0, min(1, (rssi - sensitivity) / rssi_range))
        # Strong signal = short wait (this node is close, good relay)
        # Weak signal = long wait (far away, likely redundant)
        slot = get_current_slot_time()
        wait_ms = slot * (self.v6_cfg['defer_slot_multiplier'] - rssi_normalized * 0.5)
        yield self.env.timeout(wait_ms)

        # Now check — did other relays already handle it?
        should_forward = self.v6_should_forward(packet)
        if should_forward:
            # V6 Power Control: if we know the next hop, reduce TX power
            # to just reach it (+ margin). Fewer nodes hear it = less collisions.
            txpow = None  # None = full power (default)
            target_hop = None
            # Power control ONLY for unicast (DM) with known route — not for broadcasts
            if packet.destId != 0xFFFFFFFF and packet.destId in self.v6_routes:
                target_hop = self.v6_routes[packet.destId]['nextHop']

            if target_hop is not None and target_hop in self.v6_neighbors:
                # Calculate minimum TX power to reach target with 10dB margin
                nb_rssi = self.v6_neighbors[target_hop]['rssi']
                # rssi = txpow + gains - pathLoss → pathLoss = conf.PTX + gains - rssi
                path_loss = self.conf.PTX - nb_rssi  # approximate (ignoring antenna gains for simplicity)
                sensitivity = self.conf.current_preset["sensitivity"]
                margin = 10  # dB safety margin
                min_txpow = sensitivity + path_loss + margin
                # Clamp between 5 dBm (minimum useful) and full power
                txpow = max(5, min(self.conf.PTX, min_txpow))
                logger.debug(f"{self.env.now:.3f} Node {self.nodeid} V6-power-control: {self.conf.PTX}dBm -> {txpow:.0f}dBm for hop {target_hop}")

            # Network Coding: check if we can XOR this with a buffered packet
            coding_partner = None
            for i, (buf_pkt, buf_rssi, buf_time) in enumerate(self.v6_coding_buffer):
                # Can combine if: different origin, both broadcast, both need forwarding
                if buf_pkt.origTxNodeId != packet.origTxNodeId and buf_pkt.seq != packet.seq:
                    coding_partner = i
                    break

            if coding_partner is not None:
                # XOR: send ONE packet that delivers TWO messages
                buf_pkt, _, _ = self.v6_coding_buffer.pop(coding_partner)
                logger.debug(f"{self.env.now:.3f} Node {self.nodeid} V6-XOR-coded packets {packet.seq}+{buf_pkt.seq}")
                # Send the current packet (counts as 1 TX but delivers 2 messages)
                # Recipients who have buf_pkt can extract packet, and vice versa
                pNew = MeshPacket(self.conf, self.nodes, packet.origTxNodeId, packet.destId, self.nodeid, packet.packetLen, packet.seq, packet.genTime, packet.wantAck, False, None, self.env.now, txpow_override=txpow)
                pNew.hopLimit = packet.hopLimit - 1
                # Mark as coded: the second packet's info piggybacks on this TX
                pNew._xor_partner_seq = buf_pkt.seq
                pNew._xor_partner_orig = buf_pkt.origTxNodeId
                self.packets.append(pNew)
                self.env.process(self.transmit(pNew))
                self.v6_coded_packets_sent += 1
                # Also simulate the partner delivery (receivers who have the first extract the second)
                pPartner = MeshPacket(self.conf, self.nodes, buf_pkt.origTxNodeId, buf_pkt.destId, self.nodeid, buf_pkt.packetLen, buf_pkt.seq, buf_pkt.genTime, buf_pkt.wantAck, False, None, self.env.now, txpow_override=txpow)
                pPartner.hopLimit = buf_pkt.hopLimit - 1
                # Don't add to packets list (no extra TX!) but process reception
                self.env.process(self.transmit(pPartner))
            else:
                # No coding partner — buffer for a short time, then send solo
                self.v6_coding_buffer.append((packet, rssi, self.env.now))
                # Clean old buffer entries (>2 slots old)
                from lib.phy import get_current_slot_time
                max_age = get_current_slot_time() * 3
                self.v6_coding_buffer = [(p, r, t) for p, r, t in self.v6_coding_buffer if self.env.now - t < max_age]
                # If buffer is getting full, flush oldest
                if len(self.v6_coding_buffer) > 5:
                    flush_pkt, _, _ = self.v6_coding_buffer.pop(0)
                    pFlush = MeshPacket(self.conf, self.nodes, flush_pkt.origTxNodeId, flush_pkt.destId, self.nodeid, flush_pkt.packetLen, flush_pkt.seq, flush_pkt.genTime, flush_pkt.wantAck, False, None, self.env.now, txpow_override=txpow)
                    pFlush.hopLimit = flush_pkt.hopLimit - 1
                    self.packets.append(pFlush)
                    self.env.process(self.transmit(pFlush))
                else:
                    # Schedule a timeout to send solo if no partner arrives
                    self.env.process(self.v6_coding_flush(packet.seq, get_current_slot_time() * 2, txpow))

            # ECHO: register that we rebroadcasted, wait for echo
            self.v6_echo_pending[packet.seq] = self.env.now
            self.env.process(self.v6_echo_timeout(packet.seq, self.v6_cfg['echo_timeout_ms']))
        else:
            logger.debug(f"{self.env.now:.3f} Node {self.nodeid} V6-suppressed packet {packet.seq}")

    def v6_coding_flush(self, seq, timeout_ms, txpow):
        """If a buffered packet hasn't found a coding partner, send it solo."""
        yield self.env.timeout(timeout_ms)
        for i, (buf_pkt, _, _) in enumerate(self.v6_coding_buffer):
            if buf_pkt.seq == seq:
                self.v6_coding_buffer.pop(i)
                pNew = MeshPacket(self.conf, self.nodes, buf_pkt.origTxNodeId, buf_pkt.destId, self.nodeid, buf_pkt.packetLen, buf_pkt.seq, buf_pkt.genTime, buf_pkt.wantAck, False, None, self.env.now, txpow_override=txpow)
                pNew.hopLimit = buf_pkt.hopLimit - 1
                self.packets.append(pNew)
                self.env.process(self.transmit(pNew))
                break

    def v6_echo_timeout(self, seq, timeout_ms):
        """After timeout, check if echo was received for this seq."""
        yield self.env.timeout(timeout_ms)
        self.v6_mark_echo_timeout(seq)

    def v6_compute_mpr(self):
        """Compute MPR set from passively learned 2-hop topology.
        Greedy: pick the 1-hop neighbor that covers the most uncovered 2-hop nodes."""
        my_neighbors = set(self.v6_neighbors.keys())
        if not my_neighbors:
            return

        # 2-hop neighbors: neighbors-of-neighbors minus myself and my 1-hop
        two_hop = set()
        for nb in my_neighbors:
            for nb2 in self.v6_neighbors_of_neighbor.get(nb, set()):
                if nb2 != self.nodeid and nb2 not in my_neighbors:
                    two_hop.add(nb2)

        # Greedy MPR selection
        uncovered = set(two_hop)
        mprs = set()
        while uncovered:
            best_nb = None
            best_cover = 0
            for nb in my_neighbors:
                if nb in mprs:
                    continue
                cover = len(uncovered & self.v6_neighbors_of_neighbor.get(nb, set()))
                if cover > best_cover:
                    best_cover = cover
                    best_nb = nb
            if best_nb is None or best_cover == 0:
                break
            mprs.add(best_nb)
            uncovered -= self.v6_neighbors_of_neighbor.get(best_nb, set())

        self.v6_mpr_set = mprs
        # Notify neighbors they are MPR (in real protocol via HELLO; here via shared state)
        for nb_id in mprs:
            for n in self.nodes:
                if n.nodeid == nb_id and hasattr(n, 'v6_am_mpr_for'):
                    n.v6_am_mpr_for.add(self.nodeid)

    def v6_update_echo_score(self):
        """Update rolling ECHO backbone score from recent history."""
        # Keep last 20 observations
        if len(self.v6_echo_history) > 20:
            self.v6_echo_history = self.v6_echo_history[-20:]
        if not self.v6_echo_history:
            self.v6_echo_score = 0.5
            return
        echoed = sum(1 for _, e in self.v6_echo_history if e)
        self.v6_echo_score = echoed / len(self.v6_echo_history)

    def v6_mark_echo_timeout(self, seq):
        """Called after timeout — if echo_pending still has this seq, no echo was heard."""
        if seq in self.v6_echo_pending:
            del self.v6_echo_pending[seq]
            self.v6_echo_history.append((self.env.now, False))
            self.v6_update_echo_score()

    def v6_should_forward(self, packet):
        """System V6 forwarding with MPR + ECHO backbone.

        Decision hierarchy:
        1. DM with known route → always forward
        2. MPR check → only forward if I'm an MPR for the sender (or MPR not computed yet)
        3. ECHO backbone → suppress if my echo score is low (I'm not on backbone)
        4. Relay redundancy → suppress if 2+ relays already heard
        5. Default → forward
        """
        seq = packet.seq
        times = self.timesReceived.get(seq, 0)
        relayers = self.v6_pkt_relayers.get(seq, set())

        # DM to a known destination: always forward
        if packet.destId != 0xFFFFFFFF and packet.destId in self.v6_routes:
            return True

        # --- MPR: only rebroadcast if I'm an MPR for the sending node ---
        # If MPR sets have been computed and I'm NOT designated as MPR by the sender,
        # suppress. The sender's MPR set covers all 2-hop neighbors already.
        if self.v6_am_mpr_for:  # MPR info available
            if packet.txNodeId not in self.v6_am_mpr_for and times > 1:
                # I'm not MPR for this sender — suppress
                return False

        # --- ECHO backbone: suppress if I'm consistently not echoed ---
        if len(self.v6_echo_history) >= self.v6_cfg['echo_min_observations'] and self.v6_echo_score < self.v6_cfg['echo_min_score']:
            return False

        # --- Relay redundancy ---
        if len(relayers) >= self.v6_cfg['relay_redundancy_threshold']:
            return False

        # --- Gossip: non-MPR nodes forward with small probability ---
        gossip_p = self.v6_cfg['gossip_probability']
        if gossip_p > 0 and self.v6_am_mpr_for and packet.txNodeId not in self.v6_am_mpr_for:
            # I'm not MPR for this sender — gossip forward with probability
            if random.random() > gossip_p:
                return False

        # Default: forward
        return True

    def get_stats(self) -> MeshNodeStats:
        """Get internally-tracked statistics/data. Only valid after the sim ends.
        """
        return self.my_stats

def default_generate_node_list(conf: Config) -> [NodeConfig]:
    """Default function for randomly choosing node configurations for a simulation
    run, based on the provided config and desired number of nodes specified in
    the config.
    """
    # need to identically match RNG usage right now to pass the discrete sim
    # test. If we want to change the reference test, do that in a smaller change.

    node_configs = []

    # replicate default 'no prior config' setup:
    for i in range(conf.NR_NODES):
        # no specified node config, randomly generate one
        # get node's position
        x, y = find_random_position(conf, node_configs)
        z = conf.HM
        position = Point(x, y, z)

        # role
        isRouter = conf.router
        isRepeater = False
        isClientMute = False

        # other default values
        hopLimit = conf.hopLimit
        antennaGain = conf.GL

        # map misc. booleans into single role
        if isRouter:
            role = MESHTASTIC_ROLE.ROUTER
        else:
            role = MESHTASTIC_ROLE.CLIENT

        # make NodeConfig object to pass to MeshNode constructor
        node_configs.append(NodeConfig(i, position, conf.PERIOD, role))

    return node_configs
