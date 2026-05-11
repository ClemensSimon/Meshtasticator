from lib.phy import airtime, estimate_path_loss

NODENUM_BROADCAST = 0xFFFFFFFF


class MeshPacket:
    def __init__(self, conf, nodes, origTxNodeId, destId, txNodeId, plen, seq, genTime, wantAck, isAck, requestId, now, txpow_override=None, sf_override=None, implicit_header=False, preamble_symbols=None):
        """Create a new packet and calculate which nodes sense and receive it

        Arguments:
        conf -- simulation Config
        nodes -- set of all nodes in the simulation. For calculating which nodes receive the packet
        origTxNodeId -- ID of originating node
        destId -- ID of destination node (specific node or broadcast)
        txNodeId -- ID of currently transmitting node
        plen -- packet length
        seq -- message sequence number
        genTime -- sim time of original packet generation (different from acks/rebroadcasts)
        wantAck -- this packet wants an ACK
        isAck -- this packet is an ACK packet
        requestId -- ID of packet requesting ACK (only valid for ACK packets)
        now -- current sim time when called, always `env.now`
        txpow_override -- V6 power control: if set, use this TX power instead of PTX
        """
        self.conf = conf
        self.origTxNodeId = origTxNodeId
        self.destId = destId
        self.txNodeId = txNodeId
        self.wantAck = wantAck
        self.isAck = isAck
        self.seq = seq
        self.requestId = requestId
        self.genTime = genTime
        self.now = now
        self.txpow = txpow_override if txpow_override is not None else self.conf.PTX
        self.LplAtN = [0 for _ in range(self.conf.NR_NODES)]
        self.rssiAtN = [0 for _ in range(self.conf.NR_NODES)]
        self.sensedByN = [False for _ in range(self.conf.NR_NODES)]
        self.detectedByN = [False for _ in range(self.conf.NR_NODES)]
        self.collidedAtN = [False for _ in range(self.conf.NR_NODES)]
        self.receivedAtN = [False for _ in range(self.conf.NR_NODES)]
        self.onAirToN = [True for _ in range(self.conf.NR_NODES)]

        # configuration values
        self.sf = sf_override if sf_override is not None else self.conf.current_preset["sf"]
        self.cr = self.conf.current_preset["cr"]
        self.bw = self.conf.current_preset["bw"]
        self.freq = self.conf.FREQ
        self.tx_node = next(n for n in nodes if n.nodeid == self.txNodeId)
        # SF-specific sensitivity (lower SF needs stronger signal)
        SF_SENSITIVITY = {7: -118.5, 8: -124.0, 9: -126.5, 10: -129.0, 11: -131.5, 12: -137.0}
        pkt_sensitivity = SF_SENSITIVITY.get(self.sf, self.conf.current_preset["sensitivity"])
        pkt_cad_threshold = pkt_sensitivity - 3  # CAD is 3dB less than sensitivity
        # calculate reception at all other nodes
        for rx_node in nodes:
            if rx_node.nodeid == self.txNodeId:
                continue
            dist_3d = self.tx_node.position.euclidean_distance(rx_node.position)
            offset = self.conf.LINK_OFFSET[(self.txNodeId, rx_node.nodeid)]
            self.LplAtN[rx_node.nodeid] = estimate_path_loss(self.conf, dist_3d, self.freq, self.tx_node.position.z, rx_node.position.z) + offset
            self.rssiAtN[rx_node.nodeid] = self.txpow + self.tx_node.antennaGain + rx_node.antennaGain - self.LplAtN[rx_node.nodeid]
            if self.rssiAtN[rx_node.nodeid] >= pkt_sensitivity:
                self.sensedByN[rx_node.nodeid] = True
            if self.rssiAtN[rx_node.nodeid] >= pkt_cad_threshold:
                self.detectedByN[rx_node.nodeid] = True

        self.packetLen = plen
        self.implicit_header = implicit_header
        self.preamble_symbols = preamble_symbols
        self.timeOnAir = airtime(self.conf, self.sf, self.cr, self.packetLen, self.bw,
                                 implicit_header=self.implicit_header,
                                 preamble_symbols=self.preamble_symbols)
        self.startTime = 0
        self.endTime = 0

        # Routing
        self.retransmissions = self.conf.maxRetransmission
        self.ackReceived = False
        self.hopLimit = self.tx_node.hopLimit

        # V6 Security: HMAC authentication flag
        # In real firmware: HMAC-SHA256 over header fields using channel PSK
        # In simulation: all legitimate nodes produce authenticated packets
        self.authenticated = not getattr(self.tx_node, 'is_malicious', False)


class MeshMessage:
    def __init__(self, origTxNodeId, destId, genTime, seq):
        self.origTxNodeId = origTxNodeId
        self.destId = destId
        self.genTime = genTime
        self.seq = seq
        self.endTime = 0
