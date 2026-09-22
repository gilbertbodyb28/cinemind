import zlib, struct, math, sys

def read_png(p):
    d = open(p,'rb').read(); i=8; idat=b''
    while i < len(d):
        ln = struct.unpack('>I', d[i:i+4])[0]; typ = d[i+4:i+8]; data = d[i+8:i+8+ln]; i += 12+ln
        if typ==b'IHDR': w,h,bd,ct,_,_,_ = struct.unpack('>IIBBBBB', data)
        elif typ==b'IDAT': idat += data
        elif typ==b'IEND': break
    raw = zlib.decompress(idat); ch={0:1,2:3,4:2,6:4}[ct]; bpp=ch*bd//8; stride=w*bpp
    prev=bytearray(stride); rows=[]; pos=0
    for y in range(h):
        f=raw[pos]; pos+=1
        line=bytearray(raw[pos:pos+stride]); pos+=stride
        if f:
            for x in range(stride):
                a=line[x-bpp] if x>=bpp else 0; b=prev[x]; c=prev[x-bpp] if x>=bpp else 0
                if f==1: line[x]=(line[x]+a)&255
                elif f==2: line[x]=(line[x]+b)&255
                elif f==3: line[x]=(line[x]+((a+b)>>1))&255
                else:
                    pp=a+b-c; pa=abs(pp-a); pb=abs(pp-b); pc=abs(pp-c)
                    pr=a if (pa<=pb and pa<=pc) else (b if pb<=pc else c)
                    line[x]=(line[x]+pr)&255
        rows.append(bytes(line)); prev=line
    return w,h,ch,rows

def write_png(p, w, h, rows):
    raw = b''.join(b'\x00'+bytes(r) for r in rows)
    def chunk(t, d):
        c = t+d; return struct.pack('>I', len(d))+c+struct.pack('>I', zlib.crc32(c)&0xffffffff)
    out = b'\x89PNG\r\n\x1a\n'
    out += chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0))
    out += chunk(b'IDAT', zlib.compress(raw, 9))
    out += chunk(b'IEND', b'')
    open(p,'wb').write(out)

src, dst = sys.argv[1], sys.argv[2]
S = 1024; OFF = 100; T = 824; R = 185
w,h,ch,rows = read_png(src)
assert (w,h) == (T,T), (w,h)
half = T/2.0; inner = half - R
canvas = [bytearray(S*4) for _ in range(S)]
for y in range(T):
    sy = y + OFF
    srow = rows[y]; drow = canvas[sy]
    for x in range(T):
        qx = abs(x+0.5-half) - inner
        qy = abs(y+0.5-half) - inner
        if qx > 0 and qy > 0:
            dist = math.hypot(qx, qy) - R
            cov = 0.5 - dist
            if cov <= 0: continue
            if cov > 1: cov = 1.0
        else:
            cov = 1.0
        so = x*ch; do = (x+OFF)*4
        drow[do] = srow[so]; drow[do+1] = srow[so+1]; drow[do+2] = srow[so+2]
        a = srow[so+3] if ch == 4 else 255
        drow[do+3] = int(round(a*cov))
write_png(dst, S, S, canvas)
print("skrev", dst)
