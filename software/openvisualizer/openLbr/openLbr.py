import logging
class NullHandler(logging.Handler):
    def emit(self, record):
        pass
log = logging.getLogger('openLbr')
log.setLevel(logging.ERROR)
log.addHandler(NullHandler())

from eventBus import eventBusClient
import openvisualizer_utils as u

#============================ parameters ======================================

class OpenLbr(eventBusClient.eventBusClient):
    '''
    \brief Class which is responsible for translating between 6LoWPAN and IPv6
        headers.
    
    This class implements the following RFCs:
    - http://tools.ietf.org/html/rfc6282
      Compression Format for IPv6 Datagrams over IEEE 802.15.4-Based Networks.
    - http://tools.ietf.org/html/rfc2460
      Internet Protocol, Version 6 (IPv6) Specification
    '''
    
    # http://www.iana.org/assignments/protocol-numbers/protocol-numbers.xml 
    IANA_PROTOCOL_IPv6ROUTE  = 43
    
    # Number of bytes in an IPv6 header.
    IPv6_HEADER_LEN          = 40
    
    IPHC_DISPATCH            = 3
    
    IPHC_TF_4B               = 0
    IPHC_TF_3B               = 1
    IPHC_TF_1B               = 2
    IPHC_TF_ELIDED           = 3

    IPHC_NH_INLINE           = 0
    IPHC_NH_COMPRESSED       = 1

    IPHC_HLIM_INLINE         = 0
    IPHC_HLIM_1              = 1
    IPHC_HLIM_64             = 2
    IPHC_HLIM_255            = 3 

    IPHC_CID_NO              = 0
    IPHC_CID_YES             = 1

    IPHC_SAC_STATELESS       = 0
    IPHC_SAC_STATEFUL        = 1

    IPHC_SAM_128B            = 0
    IPHC_SAM_64B             = 1
    IPHC_SAM_16B             = 2
    IPHC_SAM_ELIDED          = 3

    IPHC_M_NO                = 0
    IPHC_M_YES               = 1

    IPHC_DAC_STATELESS       = 0
    IPHC_DAC_STATEFUL        = 1

    IPHC_DAM_128B            = 0
    IPHC_DAM_64B             = 1
    IPHC_DAM_16B             = 2
    IPHC_DAM_ELIDED          = 3
    
    #=== RPL source routing header (RFC6554)
    SR_FIR_TYPE              = 0x03
    
    def __init__(self):
        
        # log
        log.debug("create instance")
        
        # store params
        
        # initialize parent class
        eventBusClient.eventBusClient.__init__(
            self,
            name             = 'OpenTun',
            registrations =  [
                {
                    'sender'   : self.WILDCARD,
                    'signal'   : 'v6ToMesh',
                    'callback' : self._v6ToMesh_notif
                }
            ]
        )
        
        # local variables
            
    #======================== public ==========================================
    
    #======================== private =========================================
    
    #===== IPv6 -> 6LoWPAN
    
    def _v6ToMesh_notif(self,sender,signal,data):
        '''
        \brief Converts a IPv6 packet into a 6LoWPAN packet.
        
        This function assumes there is a component listening on the EventBus
        which answers to the 'getSourceRoute' signal.
        
        This function dispatches the 6LoWPAN packet with signal 'bytesToMesh'.
        '''
        
        try:
            
            ipv6_bytes       = data
            
            # turn raw byte into dictionnary of fields
            ipv6             = self.disassemble_ipv6(ipv6_bytes)
            
             # filter out multicast packets
            if ipv6['dst_addr'][0]==0xff:
                return
            
            # log
            log.debug(self._format_IPv6(ipv6,ipv6_bytes))
            
            # convert IPv6 dictionnary into 6LoWPAN dictionnary
            lowpan           = self.ipv6_to_lowpan(ipv6)
            
            # add the source route to this destination
            if (len(lowpan['dst_addr'])==16):
                dst_addr=lowpan['dst_addr'][8:]
            elif (len(lowpan['dst_addr'])==8):
                dst_addr=lowpan['dst_addr']
            else:
                log.warning('unsupported address format {0}'.format(lowpan['dst_addr']))
                    
            lowpan['route'] = self._getSourceRoute(dst_addr)
            
            lowpan['route'].pop() #remove last as this is me.
            
            if not lowpan['route']:
                # no source route could be found
                log.warning('no source route to {0}'.format(lowpan['dst_addr']))
                # TODO: return ICMPv6 message
                return
            
            lowpan['nextHop'] = lowpan['route'][len(lowpan['route'])-1] #get next hop as this has to be the destination address, this is the last element on the list
            # turn dictionnary of fields into raw bytes
            lowpan_bytes     = self.reassemble_lowpan(lowpan)
            #print lowpan_bytes
            # log
            log.debug(self._format_lowpan(lowpan,lowpan_bytes))
            
            # dispatch
            self.dispatch(
                signal       = 'bytesToMesh',
                data         = (lowpan['nextHop'],lowpan_bytes),
            )
            
        except (ValueError,NotImplementedError) as err:
            log.error(err)
            pass
    
    def disassemble_ipv6(self,ipv6):
        '''
        \brief Turn byte array representing IPv6 packets into into dictionnary
            of fields.
        
        See http://tools.ietf.org/html/rfc2460#page-4.
        
        \param[in] ipv6 Byte array representing an IPv6 packet.
        
        \raises ValueError when some part of the process is not defined in
            the standard.
        \raises NotImplementedError when some part of the process is defined in
            the standard, but not implemented in this module.
        
        \return A dictionnary of fields.
        '''
        
        if len(ipv6)<self.IPv6_HEADER_LEN:
            raise ValueError('Packet too small ({0} bytes) no space for IPv6 header'.format(len(ipv6)))
        
        returnVal                      = {}
        returnVal['version']           = ipv6[0] >> 4
        if returnVal['version']!=6:
            raise ValueError('Not an IPv6 packet, version=={0}'.format(returnVal['version']))
        
        returnVal['traffic_class']     = ((ipv6[0] & 0x0F) << 4) + (ipv6[1] >> 4)
        returnVal['flow_label']        = ((ipv6[1] & 0x0F) << 16) + (ipv6[2] << 8) + ipv6[3]
        returnVal['payload_length']    = self._buf2int(ipv6[4:6])
        returnVal['next_header']       = ipv6[6]
        returnVal['hop_limit']         = ipv6[7]
        returnVal['src_addr']          = ipv6[8:8+16]
        returnVal['dst_addr']          = ipv6[24:24+16]
        returnVal['payload']           = ipv6[40:]
        
        return returnVal
    
    def ipv6_to_lowpan(self,ipv6):
        '''
        \brief Compact IPv6 header into 6LowPAN header.
        
        \param[in] ipv6 A disassembled IPv6 packet.
        
        \raises ValueError when some part of the process is not defined in
            the standard.
        \raises NotImplementedError when some part of the process is defined in
            the standard, but not implemented in this module.
        
        \return A disassembled 6LoWPAN packet.
        '''
        
        # header
        lowpan = {}
        
        # tf
        if ipv6['traffic_class']!=0:
            raise NotImplementedError('traffic_class={0} unsupported'.format(ipv6['traffic_class']))
        if ipv6['flow_label']!=0:
            raise NotImplementedError('flow_label={0} unsupported'.format(ipv6['flow_label']))
        lowpan['tf']         = []
        
        # nh
        lowpan['nh']         = [ipv6['next_header']]
        
        # hlim
        lowpan['hlim']       = [ipv6['hop_limit']]
        
        # cid
        lowpan['cid']        = []
        
        # src_addr
        lowpan['src_addr']   = ipv6['src_addr']
        
        # dst_addr
        lowpan['dst_addr']   = ipv6['dst_addr']
        
        # payload
        lowpan['payload']    = ipv6['payload']
        
        # join
        return lowpan
    
    def reassemble_lowpan(self,lowpan):
        '''
        \brief Turn dictionnary of 6LoWPAN header fields into byte array.
        
        \param[in] lowpan Dictionnary of fields representing a 6LoWPAN header.
        
        \return A list of bytes representing the 6LoWPAN packet.
        '''
        returnVal            = []
        
        # Byte1: 011(3b) TF(2b) NH(1b) HLIM(2b)
        if len(lowpan['tf'])==0:
            tf               = self.IPHC_TF_ELIDED
        else:
            raise NotImplementedError()
        if len(lowpan['nh'])==1:
            nh               = self.IPHC_NH_INLINE
        else:
            nh               = self.IPHC_NH_COMPRESSED
        if   lowpan['hlim']==1:
            hlim             = self.IPHC_HLIM_1
            lowpan['hlim'] = []
        elif lowpan['hlim']==64:
            hlim             = self.IPHC_HLIM_64
            lowpan['hlim'] = []
        elif lowpan['hlim']==255:
            hlim             = self.IPHC_HLIM_255
            lowpan['hlim'] = []
        else:
            hlim             = self.IPHC_HLIM_INLINE
        returnVal           += [(self.IPHC_DISPATCH<<5) + (tf<<3) + (nh<<2) + (hlim<<0)]
        
        # Byte2: CID(1b) SAC(1b) SAM(2b) M(1b) DAC(2b) DAM(2b)
        if len(lowpan['cid'])==0:
            cid              = self.IPHC_CID_NO
        else:
            cid              = self.IPHC_CID_YES
        sac                  = self.IPHC_SAC_STATELESS
        if   len(lowpan['src_addr'])==128/8:
            sam              = self.IPHC_SAM_128B
        elif len(lowpan['src_addr'])==64/8:
            sam              = IPHC_SAM_64B
        elif len(lowpan['src_addr'])==16/8:
            sam              = self.IPHC_SAM_16B
        elif len(lowpan['src_addr'])==0:
            sam              = self.IPHC_SAM_ELIDED
        else:
            raise SystemError()
        dac                  = self.IPHC_DAC_STATELESS
        m                    = self.IPHC_M_NO
        if   len(lowpan['dst_addr'])==128/8:
            dam              = self.IPHC_DAM_128B
        elif len(lowpan['dst_addr'])==64/8:
            dam              = self.IPHC_DAM_64B
        elif len(lowpan['dst_addr'])==16/8:
            dam              = self.IPHC_DAM_16B
        elif len(lowpan['dst_addr'])==0:
            dam              = self.IPHC_DAM_ELIDED
        else:
            raise SystemError()
        returnVal           += [(cid << 7) + (sac << 6) + (sam << 4) + (m << 3) + (dac << 2) + (dam << 0)]
        
        # tf
        returnVal           += lowpan['tf']
        
        # nh
        if len(lowpan['route'])==1:
            # destination is next hop
            returnVal       += lowpan['nh']
        else:
            # source route needed
            returnVal       += [self.IANA_PROTOCOL_IPv6ROUTE]
        
        # hlim
        returnVal           += lowpan['hlim']
        
        # cid
        returnVal           += lowpan['cid']
        
        # src_addr
        returnVal           += lowpan['src_addr']
        
        if len(lowpan['route'])>1:
            # source route needed
            if (len(lowpan['dst_addr'])==16): #this is a hack by now as the src routing table is only 8B and not 128, so I need to get the prefix from the destination address as I know are the same.
                prefix=lowpan['dst_addr'][:8]
                    
            returnVal       += prefix + lowpan['nextHop']                # dest address is next hop in source routing -- poipoi xv prefix needs to be removed once hc works well
            returnVal       += lowpan['nh']                     # Next Header
            returnVal       += [len(lowpan['route'])-1]           # Hdr Ext Len. -1 to remove last element
            returnVal       += [self.SR_FIR_TYPE]               # Routing Type. 3 for source routing
            returnVal       += [len(lowpan['route'])-1]           # Segments Left. -1 because the first hop goes to the ipv6 destination address.
            returnVal       += [0x08 << 4 | 0x08]               # CmprI | CmprE. All prefixes elided.
            returnVal       += [0x00,0x00,0x00]                 # padding (4b) + reserved (20b)
            for hop in lowpan['route'][:len(lowpan['route'])-1]:  #skip first hop as it is in the destination address
               returnVal    += hop
        
        else:# in case of 1hop destination address is the same as ipv6 destination address
             # dst_addr
             returnVal           += lowpan['dst_addr']
        
        # payload
        returnVal += lowpan['payload']
        
        return returnVal
    
    #===== 6LoWPAN -> IPv6
    
    '''
    def lowpan_to_ipv6(pkt_lowpan):
        pkt_ipv6 = dict()
        ptr = 2
        if ((ord(pkt_lowpan[0]) >> 5) != 0x003):
            errorMessage = " ERROR [lowpan_to_ipv6] not a 6LowPAN packet"
            sys.stderr.write("\n"+datetime.datetime.now().strftime("%m/%d/%Y %H:%M:%S")+errorMessage)
            print errorMessage
            return   
        # tf
        tf = (ord(pkt_lowpan[0]) >> 3) & 0x03
        if (tf == IPHC_TF_3B):
            pkt_ipv6['flow_label'] = (ord(pkt_lowpan[ptr]) << 16) + (ord(pkt_lowpan[ptr+1]) << 8) + (ord(pkt_lowpan[ptr+2]) << 0)
            ptr = ptr + 3
        elif (tf == IPHC_TF_ELIDED):
            pkt_ipv6['flow_label'] = 0
        else:
            errorMessage = " ERROR [lowpan_to_ipv6] unsupported or wrong tf"
            sys.stderr.write("\n"+datetime.datetime.now().strftime("%m/%d/%Y %H:%M:%S")+errorMessage)
            print errorMessage
        # nh
        nh = (ord(pkt_lowpan[0]) >> 2) & 0x01
        if (nh == IPHC_NH_INLINE):
            pkt_ipv6['next_header'] = ord(pkt_lowpan[ptr])
            ptr = ptr+1
        elif (nh == IPHC_NH_COMPRESSED):
            errorMessage = " ERROR [lowpan_to_ipv6] unsupported nh==IPHC_NH_COMPRESSED."
            sys.stderr.write("\n"+datetime.datetime.now().strftime("%m/%d/%Y %H:%M:%S")+errorMessage)
            print errorMessage
            pass
        else:
            errorMessage = " ERROR [lowpan_to_ipv6] wrong nh=="+str(nh)
            sys.stderr.write("\n"+datetime.datetime.now().strftime("%m/%d/%Y %H:%M:%S")+errorMessage)
            print errorMessage
        # hlim
        hlim = ord(pkt_lowpan[0]) & 0x03
        if (hlim == IPHC_HLIM_INLINE):
            pkt_ipv6['hop_limit'] = ord(pkt_lowpan[ptr])
            ptr = ptr+1
        elif (hlim == IPHC_HLIM_1):
            pkt_ipv6['hop_limit'] = 1
        elif (hlim == IPHC_HLIM_64):
            pkt_ipv6['hop_limit'] = 64
        elif (hlim == IPHC_HLIM_255):
            pkt_ipv6['hop_limit'] = 255
        else:
            errorMessage = " ERROR [lowpan_to_ipv6] wrong hlim=="+str(hlim)
            sys.stderr.write("\n"+datetime.datetime.now().strftime("%m/%d/%Y %H:%M:%S")+errorMessage)
            print errorMessage
        # sam
        sam = (ord(pkt_lowpan[1]) >> 4) & 0x03
        if (sam == IPHC_SAM_ELIDED):
            errorMessage = " ERROR [lowpan_to_ipv6] unsupported sam==IPHC_SAM_ELIDED"
            sys.stderr.write("\n"+datetime.datetime.now().strftime("%m/%d/%Y %H:%M:%S")+errorMessage)
            print errorMessage
        elif (sam == IPHC_SAM_16B):
            a1 = pkt_lowpan[ptr]
            a2 = pkt_lowpan[ptr+1]
            ptr = ptr+2
            s = ''.join(['\x00','\x00','\x00','\x00','\x00','\x00',a1,a2])
            pkt_ipv6['src_addr'] = my_openprefix.IP64B_PREFIX+s
        elif (sam == IPHC_SAM_64B):
            pkt_ipv6['src_addr'] = ''.join(my_openprefix.IP64B_PREFIX)+(pkt_lowpan[ptr:ptr+8])
            ptr = ptr + 8
        elif (sam == IPHC_SAM_128B):
            pkt_ipv6['src_addr'] = pkt_lowpan[ptr:ptr+16]
            ptr = ptr + 16
        else:
            errorMessage = " ERROR [lowpan_to_ipv6] wrong sam=="+str(sam)
            sys.stderr.write("\n"+datetime.datetime.now().strftime("%m/%d/%Y %H:%M:%S")+errorMessage)
            print errorMessage
        # dam
        dam = (ord(pkt_lowpan[1]) & 0x03)
        if (dam == IPHC_DAM_ELIDED):
            errorMessage = " ERROR [lowpan_to_ipv6] unsupported dam==IPHC_DAM_ELIDED"
            sys.stderr.write("\n"+datetime.datetime.now().strftime("%m/%d/%Y %H:%M:%S")+errorMessage)
            print errorMessage
        elif (dam == IPHC_DAM_16B):
            a1 = pkt_lowpan[ptr]
            a2 = pkt_lowpan[ptr+1]
            ptr = ptr+2
            s = ''.join(['\x00','\x00','\x00','\x00','\x00','\x00',a1,a2])
            pkt_ipv6['dst_addr'] = my_openprefix.IP64B_PREFIX+s
        elif (dam == IPHC_DAM_64B):
            pkt_ipv6['dst_addr'] = ''.join(my_openprefix.IP64B_PREFIX)+pkt_lowpan[ptr:ptr+8]
            ptr = ptr + 8
        elif (dam == IPHC_DAM_128B):
            pkt_ipv6['dst_addr'] = pkt_lowpan[ptr:ptr+16]
            ptr = ptr + 16
        else:
            errorMessage = " ERROR [lowpan_to_ipv6] wrong dam=="+str(dam)
            sys.stderr.write("\n"+datetime.datetime.now().strftime("%m/%d/%Y %H:%M:%S")+errorMessage)
            print errorMessage
        # payload
        pkt_ipv6['version']        = 6
        pkt_ipv6['traffic_class']  = 0
        pkt_ipv6['payload']        = pkt_lowpan[ptr:len(pkt_lowpan)]
        pkt_ipv6['payload_length'] = len(pkt_ipv6['payload'])
        return pkt_ipv6
    
    def reassemble_ipv6_packet(pkt):
        pktw = []
        pktw.append(chr((6 << 4) + (pkt['traffic_class'] >> 4)))
        pktw.append(chr( ((pkt['traffic_class'] & 0x0F) << 4) + (pkt['flow_label'] >> 16) ))
        pktw.append(chr( (pkt['flow_label'] >> 8) & 0x00FF ))
        pktw.append(chr( pkt['flow_label'] & 0x0000FF ))
        pktw.append(chr( pkt['payload_length'] >> 8 ))
        pktw.append(chr( pkt['payload_length'] & 0x00FF ))
        pktw.append(chr( pkt['next_header'] ))
        pktw.append(chr( pkt['hop_limit'] ))
        for i in range(0,16):
            pktw.append( pkt['src_addr'][i] )
        for i in range(0,16):
            pktw.append( pkt['dst_addr'][i] ) 
        pktws = ''.join(pktw)
        pktws = pktws + pkt['payload']
        return pktws
    '''
    
    #======================== helpers =========================================
    
    #===== source route
    
    def _getSourceRoute(self,destination):
        temp = self.dispatch(
            signal       = 'getSourceRoute', 
            data         = destination,
        )
        for (function,returnVal) in temp:
            if returnVal:
                return returnVal
        raise SystemError('No answer to signal getSourceRoute')
    
    #===== formatting
    
    def _format_IPv6(self,ipv6,ipv6_bytes):
        output  = []
        output += ['']
        output += ['']
        output += ['============================= IPv6 packet =====================================']
        output += ['']
        output += ['Version:           {0}'.format(ipv6['version'])]
        output += ['Traffic class:     {0}'.format(ipv6['traffic_class'])]
        output += ['Flow label:        {0}'.format(ipv6['flow_label'])]
        output += ['Payload length:    {0}'.format(ipv6['payload_length'])]
        output += ['Hop Limit:         {0}'.format(ipv6['hop_limit'])]
        output += ['Next header:       {0}'.format(ipv6['next_header'])]
        output += ['Source Addr.:      {0}'.format(u.formatIPv6Addr(ipv6['src_addr']))]
        output += ['Destination Addr.: {0}'.format(u.formatIPv6Addr(ipv6['dst_addr']))]
        output += ['Payload:           {0}'.format(u.formatBuf(ipv6['payload']))]
        output += ['']
        output += [self._formatWireshark(ipv6_bytes)]
        output += ['']
        return '\n'.join(output)
    
    def _format_lowpan(self,lowpan,lowpan_bytes):
        output          = []
        output         += ['']
        output         += ['']
        output         += ['============================= lowpan packet ===================================']
        output         += ['']
        output         += ['tf:                {0}'.format(u.formatBuf(lowpan['tf']))]
        output         += ['nh:                {0}'.format(u.formatBuf(lowpan['nh']))]
        output         += ['hlim:              {0}'.format(u.formatBuf(lowpan['hlim']))]
        output         += ['cid:               {0}'.format(u.formatBuf(lowpan['cid']))]
        output         += ['src_addr:          {0}'.format(u.formatBuf(lowpan['src_addr']))]
        output         += ['dst_addr:          {0}'.format(u.formatBuf(lowpan['dst_addr']))]
        if 'route' in lowpan:
            output     += ['source route:']
            for hop in lowpan['route']:
                output += [' - {0}'.format(u.formatAddr(hop))]
        output         += ['payload:           {0}'.format(u.formatBuf(lowpan['payload']))]
        output += ['']
        output += [self._formatWireshark(lowpan_bytes)]
        output += ['']
        return '\n'.join(output)
    
    def _formatWireshark(self,pkt):
        NUM_BYTES_PER_LINE        = 16
        
        output                    = []
        index                     = 0
        while index<len(pkt):
            this_line             = []
            
            # get the bytes for this line
            bytes                 = pkt[index:index+NUM_BYTES_PER_LINE]
            
            # print the header
            this_line            += ['%06x '%index]
            
            # print the bytes (gather the end_chars)
            end_chars             = []
            end_chars            += ['  ']
            for b in bytes:
                # print the bytes
                this_line        += [' %02x'%b]
                # gather the end_chars
                if b>32 and b<127:
                    end_chars    += [chr(b)]
                else:
                    end_chars    += ['.']
            
            # pad
            for _ in range(len(bytes),NUM_BYTES_PER_LINE):
                this_line        += ['   ']
            
            # print the end_chars
            this_line            += end_chars
            
            # store the line
            this_line             = ''.join(this_line)
            output               += [this_line]
            
            # increment index
            index                += NUM_BYTES_PER_LINE
        
        return '\n'.join(output)
    
    #===== misc
    
    @classmethod
    def _buf2int(self,buf,startBit=None,numBits=None):
        '''
        \brief Converts some consecutive bytes of a buffer into an integer.
        
        \note Big-endianness is assumed.
        
        \param[in] buf      Byte array.
        \param[in] startBit Bit to start at.
        \param[in] numBits  Number of bits of interest.
        '''
        returnVal = 0
        for i in range(len(buf)):
            returnVal += buf[i]<<(8*(len(buf)-i-1))
        return returnVal
