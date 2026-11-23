from PIL import Image, ImageDraw
def icon(size, pad_ratio, path):
    img = Image.new("RGBA", (size, size), (11,15,20,255))
    d = ImageDraw.Draw(img)
    p = int(size*pad_ratio)
    d.rounded_rectangle([p,p,size-p,size-p], radius=int(size*0.22), fill=(198,255,61,255))
    s = size - 2*p
    pts = [(0.58,0.12),(0.26,0.56),(0.47,0.56),(0.40,0.88),(0.74,0.42),(0.53,0.42),(0.60,0.12)]
    d.polygon([(p+x*s, p+y*s) for x,y in pts], fill=(11,15,20,255))
    img.save(path)
icon(192, 0.06, "icon-192.png"); icon(512, 0.06, "icon-512.png")
icon(512, 0.16, "maskable-512.png"); icon(180, 0.0, "apple-touch-icon.png")
